#!/usr/bin/env python3
"""
judge_costopt.py — Step 3 闲置资源判定脚本

将所有产品的闲置判定、月省计算、advisor 双源合并固化为脚本，
消灭 Step 3 碎片 exec（每次 3.2s × 30 次 = 96s）。

用法:
  python3 judge_costopt.py \
    --work-dir /tmp/costopt_YIZHONG \
    --account-name "一中心管控账户"

前置（F-STEP3-PREREQ）:
  work-dir 内必须存在:
    resource_discovery.json  (Step 1 产物)
    billing_lookup.json      (Step 0.4 产物)
    monitor_summary.json     (Step 2 产物)
    advisor_cost.json        (Step 1.5 产物)

输出:
  stdout: 判定摘要 JSON（findings 数、总月省、各产品计数）
  文件: {work-dir}/step3_results.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, date

# ─── YAML 规则加载 ──────────────────────────────────────────
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_RULES_CACHE = None

def _load_product_rules():
    """加载 product_rules.yaml，返回 dict。"""
    global _RULES_CACHE
    if _RULES_CACHE is not None:
        return _RULES_CACHE
    rules_path = os.path.join(os.path.dirname(__file__), "product_rules.yaml")
    if _HAS_YAML and os.path.exists(rules_path):
        with open(rules_path, "r", encoding="utf-8") as f:
            _RULES_CACHE = yaml.safe_load(f) or {}
    else:
        _RULES_CACHE = {}
    return _RULES_CACHE

# ─── CBS 刊例价（F41）────────────────────────────────────────────
CBS_PRICE_PER_GB = {
    "CLOUD_PREMIUM": 0.35,
    "CLOUD_SSD":     1.10,
    "CLOUD_BSSD":    0.50,
    "CLOUD_HSSD":    0.35,
    "CLOUD_TSSD":    1.80,
}

# ─── pricing-rules.md §三 优化路径 → ratio 映射 ─────────────────
RATIO_MAP = {
    # CVM
    "cvm_release":       1.0,
    "cvm_downsize_half": 0.5,
    "cvm_to_annual":     0.4,
    "cvm_to_monthly":    0.25,
    "cvm_scheduled_off": 0.57,
    "cvm_spot":          0.8,
    # CBS
    "cbs_release":       1.0,
    "cbs_type_downgrade":0.5,
    # MySQL
    "mysql_release":     1.0,
    "mysql_downsize":    0.4,
    "mysql_to_annual":   0.4,
    # Redis
    "redis_release":     1.0,
    "redis_downsize":    0.4,
    # MongoDB
    "mongo_release":     1.0,
    "mongo_downsize":    0.3,
    # PostgreSQL
    "pgsql_release":     1.0,
    "pgsql_downsize":    0.35,
    # EIP / CLB
    "eip_release":       1.0,
    "clb_release":       1.0,
    # NAT / VPN
    "nat_downsize":      0.3,
    "vpn_downsize":      0.3,
    # ES
    "es_release":        1.0,
    "es_downsize":       0.35,
    # CKafka
    "ckafka_release":    1.0,
    "ckafka_downsize":   0.3,
    # CFS
    "cfs_release":       1.0,
    "cfs_type_downgrade":0.35,
    # COS
    "cos_infrequent":    0.32,
    "cos_archive":       0.72,
    "cos_deep_archive":  0.90,
    # TKE
    "tke_release":       1.0,
    "tke_downsize":      0.4,
    # TDMQ
    "tdmq_release":      1.0,
    # VectorDB
    "vecdb_release":     1.0,
    "vecdb_downsize":    0.4,
    # LH (advisor only)
    "lh_advisor":        0.3,
    # EMR
    "emr_release":       1.0,
    "emr_downsize":      0.4,
    # DC
    "dc_downsize":       0.2,
    # CCN
    "ccn_release":       1.0,
    "ccn_downsize":      0.2,
}

# ─── advisor Level → 严重度映射（§7.1）──────────────────────────
def map_advisor_level(hit):
    level = hit.get("level", 1)
    risk_days = hit.get("risk_days", 0)
    strategy_name = (hit.get("strategy_name", "") or "").lower()
    # 计费模式类策略（按量长期运行）升级为 critical
    billing_keywords = ("计费模式", "按量", "billing", "charge_type", "包年包月", "转包")
    is_billing = any(kw in strategy_name for kw in billing_keywords)
    if level >= 3:
        return "critical" if is_billing else "high"
    if level == 2 and risk_days > 30:
        return "critical" if is_billing else "medium_high"
    if level == 2:
        return "medium_high" if is_billing else "medium"
    return "low"


SEVERITY_ORDER = {"critical": 5, "high": 4, "medium_high": 3, "medium": 2, "low": 1}

SEVERITY_RISK_LABEL = {
    "critical":     "🔴 [严重] 建议立即处理",
    "high":         "🔴 [高] 请评审后执行",
    "medium_high":  "🟠 [中] 建议处理",
    "medium":       "🟡 [中] 关注观察",
    "low":          "🟢 [低]",
}

# ─── 优化路径标准化映射 ──────────────────────────────────────────

OPTIMIZE_PATH_MAP = {
    # CVM
    "停机按量计费":              "释放",
    "疑似闲置":                  "释放",
    "疑似闲置（内存数据缺失）":  "释放",
    "低利用率":                  "降配",
    "按量长期运行":              "计费优化",
    "可中断工作负载":            "计费优化",
    "非生产可定时关机":          "定时关机",
    # CBS
    "未挂载":                    "释放",
    "SSD低IO可降级":            "类型降级",
    # MySQL / CynosDB / MariaDB
    "闲置":                      "释放",
    "低利用率可降配":            "降配",
    # Redis
    "内存低可降配":              "降配",
    # CLB
    "无监听器":                  "释放",
    "无后端绑定":                "释放",
    "有后端但无连接":            "确认停用",
    "连接数极低":                "确认合并",
    # EIP
    "未绑定":                    "释放",
    # CFS
    "无客户端连接":              "释放",
    "近无IO":                    "释放",
    "性能型低IO可降级":         "类型降级",
    # TKE
    "空集群":                    "释放",
    # COS
    "极低访问频率":              "存储降级",
    "低访问频率":                "存储降级",
    # EMR
    "已终止仍计费":              "退订释放",
    # DC
    "带宽利用率低":              "缩减带宽",
    # Advisor 常见 issue_type
    "云服务器（CVM）实例利用率不足":  "降配",
    "云数据库（MySQL）利用率不足":    "降配",
}


def _issue_to_optimize_path(issue_type):
    """将 issue_type 映射为简短标准化优化路径分类。
    先精确匹配 OPTIMIZE_PATH_MAP，再按关键词兜底。"""
    if issue_type in OPTIMIZE_PATH_MAP:
        return OPTIMIZE_PATH_MAP[issue_type]
    it = (issue_type or "")
    if not it:
        return "优化"
    # 关键词兜底（按优先级排序）
    if "停机" in it:
        return "释放"
    if "闲置" in it:
        return "释放"
    if any(k in it for k in ("未挂载", "未绑定", "空集群", "无监听器", "无后端绑定",
                              "无客户端连接", "近无IO", "已终止")):
        return "释放"
    if any(k in it for k in ("按量长期", "可中断")):
        return "计费优化"
    if "定时关机" in it:
        return "定时关机"
    if "降级" in it:
        return "类型降级"
    if any(k in it for k in ("访问频率", "归档", "低频")):
        return "存储降级"
    if any(k in it for k in ("低利用率", "降配")):
        return "降配"
    if "带宽" in it:
        return "缩减带宽"
    if any(k in it for k in ("无连接", "连接数极低")):
        return "确认停用"
    if "退订" in it:
        return "退订释放"
    return it


# ─── 工具函数 ──────────────────────────────────────────────────────

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_tag(tags, key):
    """从标签列表中取值，大小写不敏感。支持多种腾讯云 Tag 格式：
    - CVM: {tagKey, tagValue} 或 {Key, Value}
    - MySQL/CLB/Redis: {TagKey, TagValue}
    - CBS: {Key, Value}
    """
    if not tags:
        return None
    key_lower = key.lower()
    for t in tags:
        # 按优先级尝试所有可能的 key 字段名
        k = (t.get("tagKey") or t.get("TagKey") or t.get("Key") or "").lower()
        if k == key_lower:
            return t.get("tagValue") or t.get("TagValue") or t.get("Value") or ""
    return None


def coverage_rate(values):
    """计算监控覆盖率（有效点数/720）。"""
    if not values:
        return 0.0
    valid = len([v for v in values if v is not None and v != ""])
    return valid / 720.0


def multi_coverage(metrics_dict):
    """多指标取最小覆盖率（F8）。"""
    rates = [coverage_rate(v) for v in metrics_dict.values() if v]
    return min(rates) if rates else 0.0


def coverage_note(cov):
    """根据覆盖率返回标注文本。"""
    if cov >= 0.8:
        return ""
    if cov >= 0.5:
        return "[数据不足，仅供参考]"
    return "[监控数据严重不足，无法判定]"


def safe_avg(values):
    """计算非 None 值的均值。"""
    valid = [v for v in (values or []) if v is not None and v != ""]
    if not valid:
        return None
    return sum(float(v) for v in valid) / len(valid)


def safe_max(values):
    valid = [v for v in (values or []) if v is not None and v != ""]
    return max(float(v) for v in valid) if valid else None


def get_monitor(monitor, rid, *metric_keys):
    """从 monitor_summary 取指定资源的指标数据，返回 {metric: [values]}。"""
    if not monitor or "instances" not in monitor:
        return {}
    inst_data = monitor.get("instances", {}).get(rid, {})
    result = {}
    for key in metric_keys:
        pts = inst_data.get(key, {}).get("points") or inst_data.get(key, [])
        result[key] = pts if isinstance(pts, list) else []
    return result


# ─── F9 豁免检查 ──────────────────────────────────────────────────

def check_exemption(inst, product_key):
    """
    检查 finops-skip 标签语义。
    返回: 'full_skip' | 'no_downgrade' | 'relax' | None
    """
    tags = (inst.get("Tags") or inst.get("TagList") or
            inst.get("InstanceTags") or [])
    skip_val = get_tag(tags, "finops-skip")
    expire_val = get_tag(tags, "finops-skip-expire")

    # 检查过期
    if expire_val:
        try:
            expire_date = date.fromisoformat(expire_val)
            if date.today() > expire_date:
                return None  # 豁免已过期
        except (ValueError, TypeError):
            pass

    if not skip_val:
        return None

    skip_lower = skip_val.lower().strip()
    if skip_lower == "true":
        return "full_skip"
    if skip_lower == "downgrade":
        return "no_downgrade"
    if skip_lower == "relax":
        return "relax"
    # 分产品豁免（逗号分隔）
    skip_products = [s.strip().lower() for s in skip_lower.split(",")]
    if product_key.lower() in skip_products:
        return "full_skip"
    return None


def apply_relax(risk_label):
    """F9 relax：风险等级降一级。"""
    if "高" in risk_label:
        return risk_label.replace("[高]", "[中]").replace("🔴", "🟡")
    if "中" in risk_label:
        return risk_label.replace("[中]", "[低]").replace("🟡", "🟢")
    return risk_label


# ─── head_map 预构建（F38）────────────────────────────────────────

def build_head_map(disc):
    head_map = {}
    for prod_key, regions in (disc.get("results") or {}).items():
        if not isinstance(regions, dict):
            continue
        for region, instances in regions.items():
            if not isinstance(instances, list):
                continue
            for inst in instances:
                rid = (inst.get("InstanceId") or inst.get("InstanceID") or
                       inst.get("DiskId") or inst.get("DBInstanceId") or
                       inst.get("LoadBalancerId") or inst.get("AddressId") or
                       inst.get("NatGatewayId") or inst.get("FileSystemId") or
                       inst.get("ClusterId") or "")
                tags = (inst.get("Tags") or inst.get("TagList") or
                        inst.get("InstanceTags") or [])
                head = get_tag(tags, "head")
                if rid and head:
                    head_map[rid] = head
    return head_map


# ─── 月省计算（三级绑价）─────────────────────────────────────────

def calc_saving(rid, product_code, ratio_key, lookup, bill_summary, disc_count=None, advisor_hit=None):
    """
    三级绑价查找。product_code 可以是产品码（"p_cdb"）或规范标题（"MySQL"）。
    Returns: (saving_int_or_None, saving_display_str)
    """
    ratio = RATIO_MAP.get(ratio_key, 1.0)

    # 级别1: billing_lookup by rid
    baseline = lookup.get(rid, {}).get("cost")
    if baseline and float(baseline) > 0:
        saving = int(float(baseline) * ratio)
        ratio_pct = int(ratio * 100)
        return saving, f"¥{saving:,} (=¥{int(baseline):,}×{ratio_pct}%)"

    # 级别2: 产品均摊
    # product_code 可能是规范标题，需先转产品码
    pc = product_code
    if not str(pc).startswith("p_"):
        # 尝试从 PRODUCT_JUDGE_MAP 取产品码
        canon = resolve_product_title(pc)
        entry = PRODUCT_JUDGE_MAP.get(canon)
        if entry:
            pc = entry[4]

    prod_costs = bill_summary.get("product_costs")
    if prod_costs is None:
        # 兼容 bill_summary.json 的 product_totals 键名
        prod_costs = bill_summary.get("product_totals")
    if prod_costs is None:
        # billing_lookup 是 rid-level 映射，需动态聚合 product_costs
        prod_costs = {}
        for key, val in (bill_summary or {}).items():
            if isinstance(val, dict) and "product" in val and "cost" in val:
                p = val["product"]
                c = float(val["cost"]) if val["cost"] else 0
                if c > 0:
                    prod_costs[p] = prod_costs.get(p, 0) + c

    # 尝试当前 pc，以及 PRODUCT_CODE_MAP 的反查（处理 LH 等多产品码产品）
    prod_total = None
    tried_pcs = [pc]
    # LH 等产品可能有多个产品码（p_svh + p_lmp），合并计算
    MULTI_CODE_PRODUCTS = {
        "轻量应用服务器": ["p_svh", "p_lmp"],
    }
    canon_for_multi = resolve_product_title(product_code)
    if canon_for_multi in MULTI_CODE_PRODUCTS:
        tried_pcs = MULTI_CODE_PRODUCTS[canon_for_multi]
    total_cost = 0.0
    for try_pc in tried_pcs:
        entry = prod_costs.get(try_pc, {}) if isinstance(prod_costs, dict) else {}
        cost = entry.get("cost") if isinstance(entry, dict) else entry
        if cost:
            total_cost += float(cost)
    if total_cost > 0:
        prod_total = total_cost

    if prod_total and disc_count and int(disc_count) > 0:
        avg = float(prod_total) / int(disc_count)
        saving = int(avg * ratio)
        ratio_pct = int(ratio * 100)
        return saving, (f"¥{saving:,} (=¥{int(prod_total):,}均摊÷{disc_count}×{ratio_pct}%)")

    # 级别3: 无数据
    return None, "需评审"


def calc_saving_cbs(inst):
    """CBS 专用刊例价月省计算（F41）。"""
    disk_size = inst.get("DiskSize", 0) or 0
    disk_type = inst.get("DiskType", "CLOUD_PREMIUM")
    unit_price = CBS_PRICE_PER_GB.get(disk_type, 0.35)
    saving = int(disk_size * unit_price)
    if saving > 0:
        return saving, f"¥{saving:,} (={disk_size}GB×¥{unit_price}/GB·月，刊例价估算)"
    return None, "需评审"


def _estimate_cbs_monthly_cost(inst):
    """从 CVM 实例的磁盘信息估算 CBS 月费（¥/月）。"""
    total_cost = 0.0
    # 单价表 (¥/GB/月) — 按腾讯云刊例价近似
    price_map = {
        "LOCAL_BASIC": 0.0,      # 本地盘不计费
        "LOCAL_SSD": 0.0,
        "CLOUD_BASIC": 0.35,
        "CLOUD_PREMIUM": 0.35,
        "CLOUD_SSD": 0.7,
        "CLOUD_BSSD": 0.9,
        "CLOUD_TSSD": 1.0,
        "CLOUD_HSSD": 1.2,
    }
    # SystemDisk
    sys_disk = inst.get("SystemDisk") or {}
    if sys_disk:
        size = sys_disk.get("DiskSize", 0) or 0
        dtype = sys_disk.get("DiskType", "CLOUD_PREMIUM")
        price = price_map.get(dtype, 0.35)
        total_cost += size * price
    # DataDisks
    data_disks = inst.get("DataDisks") or []
    for disk in data_disks:
        size = disk.get("DiskSize", 0) or 0
        dtype = disk.get("DiskType", "CLOUD_PREMIUM")
        price = price_map.get(dtype, 0.35)
        total_cost += size * price
    return total_cost


def calc_saving_cvm_stopped(rid, inst, lookup, bill_summary, disc_count):
    """CVM 停机按量月省计算。

    停机后 CBS 仍计费，月省 = 实例 baseline - 挂载 CBS 月费。
    1. 先从 billing_lookup / 产品均摊获取 baseline；
    2. 再从 billing_lookup 拆出 CBS 费用，或按实例磁盘容量×单价精确估算；
    3. 无法估算时回退 baseline × 0.9（保守扣 10% 磁盘费）。
    """
    # 步骤1: 获取 baseline（优先 billing_lookup，其次产品均摊）
    baseline_entry = lookup.get(rid, {})
    baseline = baseline_entry.get("cost")
    if baseline and float(baseline) > 0:
        baseline_val = float(baseline)
        baseline_src = f"¥{int(baseline_val):,}"
    else:
        # billing_lookup 无效（负数/None），降级到产品均摊
        saving_fallback, _ = calc_saving(rid, "p_cvm", "cvm_release", lookup, bill_summary, disc_count)
        if saving_fallback and saving_fallback > 0:
            baseline_val = float(saving_fallback)
            baseline_src = f"均摊¥{int(baseline_val):,}"
        else:
            return None, "需评审"

    # 步骤2: 获取 CBS 费用
    cbs_cost = baseline_entry.get("cbs_cost")
    if cbs_cost and float(cbs_cost) > 0:
        saving = max(0, int(baseline_val - float(cbs_cost)))
        return saving, f"¥{saving:,} (={baseline_src} - CBS¥{int(float(cbs_cost)):,})"

    # 按实例磁盘信息精确估算 CBS 费用
    est_cbs = _estimate_cbs_monthly_cost(inst)
    if est_cbs > 0:
        saving = max(0, int(baseline_val - est_cbs))
        return saving, f"¥{saving:,} (={baseline_src} - CBS估¥{int(est_cbs):,})"

    # 回退：保守扣 10%
    saving = int(baseline_val * 0.9)
    return saving, f"¥{saving:,} (={baseline_src}×90%，已扣除约10%磁盘费)"


# ─── F7 关键资源保护 ──────────────────────────────────────────────

def is_protected_f7(inst, product):
    """返回 (is_protected, reason)。"""
    # 通用：finops-critical=true
    tags = (inst.get("Tags") or inst.get("TagList") or
            inst.get("InstanceTags") or [])
    if get_tag(tags, "finops-critical") == "true":
        return True, "finops-critical=true"

    p = product.lower()
    if p in ("mysql", "cdb"):
        instance_type = inst.get("InstanceType")
        if instance_type in (2, 3, "2", "3"):
            label = "灾备实例" if str(instance_type) == "2" else "只读实例"
            return True, f"{label}（InstanceType={instance_type}）"
        # 标签兜底
        role = get_tag(tags, "role") or ""
        if role.lower() in ("readonly", "replica", "standby", "dr", "disaster", "backup"):
            return True, f"角色标签 role={role}"

    elif p == "redis":
        node_set = inst.get("NodeSet") or []
        for node in node_set:
            # NodeType: 0=master, 1=replica（整数）
            node_type = node.get("NodeType")
            if node_type == 1 or str(node_type).lower() == "replica":
                return True, "集群副本节点（NodeType=replica）"

    elif p == "mongodb":
        replica_sets = inst.get("ReplicaSetList") or []
        for rs in replica_sets:
            if rs.get("Role", "").upper() == "SECONDARY":
                return True, "副本节点（Role=SECONDARY）"

    elif p in ("postgresql", "postgres"):
        db_type = inst.get("DBInstanceType", "")
        if db_type in ("readonly", "guard"):
            return True, f"只读/灾备实例（DBInstanceType={db_type}）"

    return False, ""


# ─── 辅助：CVM Memory 单位判定 ────────────────────────────────────

def _cvm_mem_gb(mem_val):
    """CVM Memory 字段：腾讯云 CVM DescribeInstances API 返回的单位固定为 GB。
    参考: https://cloud.tencent.com/document/api/213/15753
    """
    if not mem_val or mem_val <= 0:
        return "?"
    return int(mem_val)


def _mysql_mem_gb(mem_val):
    """MySQL Memory 字段：腾讯云返回近似 GB 的 MB 值（1000MB=1GB, 2000MB=2GB, 4000MB=4GB 等），
    除以 1000 而非 1024 才能得到正确规格。
    """
    if not mem_val or mem_val <= 0:
        return "?"
    return int(mem_val / 1000)


# ─── 辅助：建议规格计算 ───────────────────────────────────────────

def _cvm_target_spec(cpu_cores, mem_gb, issue_type):
    """根据当前 CVM 规格和问题类型计算建议目标规格。"""
    if issue_type in ("停机按量计费", "疑似闲置", "疑似闲置（内存数据缺失）"):
        return "释放实例(创建快照备份)"
    if issue_type == "按量长期运行":
        return "转包年包月"
    if issue_type == "可中断工作负载":
        return "转竞价实例（SPOTPAID）"
    if issue_type == "非生产可定时关机":
        return "配置定时关机策略"
    if issue_type in ("低利用率", "云服务器（CVM）实例利用率不足"):
        # 降配至当前规格的一半，向上取最近标准规格
        target_cpu = max(1, cpu_cores // 2)
        target_mem = max(1, mem_gb // 2)
        # 标准内存配对：1→1, 2→2, 2→4, 4→4, 4→8, 8→8, 8→16...
        std_mem_pairs = {1: 1, 2: 2, 2: 4, 4: 4, 4: 8, 8: 8, 8: 16, 16: 16, 16: 32, 32: 32, 32: 64}
        # 取最接近的标准配对
        target_mem = target_mem if target_mem >= 1 else 1
        return f"降至 {target_cpu}核{target_mem}GB"
    return "-"


def _mysql_target_spec(cpu_cores, mem_gb, issue_type):
    """根据当前 MySQL 规格和问题类型计算建议目标规格。
    腾讯云 MySQL 标准规格：1核1GB, 1核2GB, 2核4GB, 2核8GB, 4核8GB, 4核16GB, 8核16GB, 8核32GB..."""
    if issue_type == "闲置":
        return "释放实例"
    if issue_type == "按量长期运行":
        return "转包年包月"
    if issue_type in ("低利用率可降配", "云数据库（MySQL）利用率不足"):
        # 标准规格档位（cpu, mem）
        tiers = [(1, 1), (1, 2), (2, 4), (2, 8), (4, 8), (4, 16), (8, 16), (8, 32), (16, 32), (16, 64)]
        # 找当前规格所在档位，降一档
        current_idx = None
        for i, (tc, tm) in enumerate(tiers):
            if tc >= cpu_cores and tm >= mem_gb:
                current_idx = i
                break
        if current_idx is not None and current_idx > 0:
            tc, tm = tiers[current_idx - 1]
            return f"降至 {tc}核{tm}GB"
        if current_idx == 0:
            return "已达最小规格"
        return "建议降配"
    return "-"


# ─── 辅助：地域提取 ───────────────────────────────────────────────

def _extract_region(inst, product):
    """从实例数据中提取地域码。"""
    # 优先取注入的地域 key（来自 resource_discovery 的 region dict key）
    rk = inst.get("_region_key")
    if rk and isinstance(rk, str) and ("ap-" in rk or "eu-" in rk or "us-" in rk):
        return rk
    p = product.lower()
    # 直接 Region 字段
    for k in ("Region", "region"):
        v = inst.get(k)
        if v and isinstance(v, str) and ("ap-" in v or "eu-" in v or "us-" in v):
            return v
    # CVM: Placement.Zone 前缀
    placement = inst.get("Placement") or {}
    zone = placement.get("Zone") or ""
    if zone:
        # ap-shanghai-5 → ap-shanghai
        parts = zone.rsplit("-", 1)
        if len(parts) == 2 and parts[0].startswith("ap-"):
            return parts[0]
        return zone
    return "-"


# ─── 辅助：baseline 提取 ──────────────────────────────────────────

def _extract_baseline(rid, lookup, bill_summary, product_code, disc_count):
    """三级绑价 baseline 提取，返回 float。"""
    # 级别1: billing_lookup by rid
    baseline = lookup.get(rid, {}).get("cost")
    if baseline and float(baseline) > 0:
        return float(baseline)
    # 级别2: 产品均摊
    prod_costs = bill_summary.get("product_costs") or bill_summary.get("product_totals") or {}
    total_cost = 0.0
    for try_pc in [product_code]:
        entry = prod_costs.get(try_pc, {})
        cost = entry.get("cost") if isinstance(entry, dict) else entry
        if cost:
            total_cost += float(cost)
    if total_cost > 0 and disc_count and int(disc_count) > 0:
        return round(total_cost / int(disc_count), 2)
    return 0


# ─── 产品判定函数 ──────────────────────────────────────────────────

def judge_cvm(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CVM 多路径判定（analysis-rules.md §5.1 共 6 条优化路径）。"""
    rid = inst.get("InstanceId", "")
    name = inst.get("InstanceName", "")
    cpu_cores = inst.get("CPU", 0) or 0
    mem_gb = _cvm_mem_gb(inst.get("Memory", 0))
    spec = f"{cpu_cores}核{mem_gb}GB"
    charge = inst.get("InstanceChargeType", "")
    state = inst.get("InstanceState", "")

    findings = []

    # ── 路径1: 停机按量 → 严重 ──
    # 停机后 CBS 仍计费，月省 = baseline - CBS费用
    if state == "STOPPED" and charge == "POSTPAID_BY_HOUR":
        saving, saving_display = calc_saving_cvm_stopped(rid, inst, lookup, bill_summary, disc_count)
        stop_charging = inst.get("StopChargingMode", "")
        if stop_charging == "STOP_CHARGING":
            # 已是关机不计费模式，仅磁盘在收费
            suggestion = "创建快照备份后释放实例（仅磁盘继续计费，如不再使用请释放）"
            target_spec = "释放实例(创建快照备份)"
        else:
            # KEEP_CHARGING：关机仍计费，建议切换
            suggestion = "切换为关机不计费模式，或创建快照备份后释放实例"
            target_spec = "释放实例(创建快照备份)"
        findings.append({
            "issue_type": "停机按量计费",
            "suggestion": suggestion,
            "target_spec": target_spec,
            "severity": "critical",
            "risk": "[严重] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })
        return findings

    if state != "RUNNING":
        return findings

    # ── 获取监控数据 ──
    cpu_pts = (mon.get("CPUUsage") or {}).get("points", [])
    mem_pts = (mon.get("MemUsage") or {}).get("points", [])

    cpu_avg = safe_avg(cpu_pts)
    mem_avg = safe_avg(mem_pts)
    cpu_max = safe_max(cpu_pts)
    mem_max = safe_max(mem_pts)

    # 覆盖率
    metrics = {"CPUUsage": cpu_pts}
    if mem_pts:
        metrics["MemUsage"] = mem_pts
    cov = multi_coverage(metrics)
    cov_note = coverage_note(cov)

    # ── 路径2: 疑似闲置（CPU<5% AND 内存<20%） ──
    if cpu_avg is not None and cpu_avg < 5:
        if mem_avg is not None and mem_avg < 20:
            if cov >= 0.5:
                saving, saving_display = calc_saving(rid, "p_cvm", "cvm_release", lookup, bill_summary, disc_count)
                # 大规格标注（≥16核）
                big_note = " ⚠️ 大规格实例（≥16核），成本损耗高，优先核查" if cpu_cores >= 16 else ""
                findings.append({
                    "issue_type": "疑似闲置",
                    "suggestion": f"降配或释放{big_note}",
                    "target_spec": _cvm_target_spec(cpu_cores, mem_gb, "疑似闲置"),
                    "severity": "high",
                    "risk": "[高] 不可逆 · 维护窗口",
                    "saving": saving,
                    "saving_display": saving_display,
                    "coverage": round(cov, 3),
                    "coverage_note": cov_note,
                })
                return findings
        elif mem_avg is None and cpu_avg < 3:
            # 内存数据缺失兜底
            saving, saving_display = calc_saving(rid, "p_cvm", "cvm_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "疑似闲置（内存数据缺失）",
                "suggestion": "降配或释放，请人工核查内存使用",
                "target_spec": _cvm_target_spec(cpu_cores, mem_gb, "疑似闲置（内存数据缺失）"),
                "severity": "medium",
                "risk": "[中] 可逆 · 业务低峰",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": "[内存数据缺失]",
            })
            return findings

    # ── 路径3: 低利用率（CPU<15% AND 内存<40%），排除峰值高的 ──
    if (cpu_avg is not None and cpu_avg < 15 and
            mem_avg is not None and mem_avg < 40 and
            (cpu_max is None or cpu_max < 60) and
            (mem_max is None or mem_max < 80)):
        if cov >= 0.5:
            saving, saving_display = calc_saving(rid, "p_cvm", "cvm_downsize_half", lookup, bill_summary, disc_count)
            # 大规格标注（≥16核）
            big_note = " ⚠️ 大规格实例（≥16核），成本损耗高，优先核查" if cpu_cores >= 16 else ""
            findings.append({
                "issue_type": "低利用率",
                "suggestion": f"降配至当前规格的 1/2（{spec}）{big_note}",
                "target_spec": _cvm_target_spec(cpu_cores, mem_gb, "低利用率"),
                "severity": "medium",
                "risk": "[中] 可逆 · 业务低峰",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    # ── 路径4: 按量长期运行 → 转包年包月 ──
    if charge == "POSTPAID_BY_HOUR":
        created = inst.get("CreatedTime", "")
        try:
            from datetime import datetime, timezone
            ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - ct).days
        except Exception:
            age_days = 999
        if age_days >= 30:
            saving, saving_display = calc_saving(rid, "p_cvm", "cvm_to_annual", lookup, bill_summary, disc_count)
            note = "低负载，确认保留后再转包" if (cpu_avg is not None and cpu_avg < 5) else "正常使用中"
            findings.append({
                "issue_type": "按量长期运行",
                "suggestion": f"转包年包月（{note}）",
                "target_spec": "转包年包月",
                "severity": "critical",
                "risk": "[严重] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": None,
                "coverage_note": "",
            })

    # ── 路径5: 可中断工作负载 → 竞价实例建议 ──
    tags = (inst.get("Tags") or [])
    env_tag = get_tag(tags, "Environment") or get_tag(tags, "env") or ""
    name_lower = (name or "").lower()
    is_dev = (env_tag.lower() in ("dev", "staging", "test", "uat") or
              any(kw in name_lower for kw in ("test", "dev", "ci", "staging", "uat")))
    if is_dev and charge == "POSTPAID_BY_HOUR" and state == "RUNNING":
        saving, saving_display = calc_saving(rid, "p_cvm", "cvm_spot", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "可中断工作负载",
            "suggestion": "建议转竞价实例（非生产环境，可承受中断）",
            "target_spec": "转竞价实例（SPOTPAID）",
            "severity": "low",
            "risk": "[低] 可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })

    # ── 路径6: 非生产定时开关机 ──
    if is_dev and charge != "SPOTPAID" and state == "RUNNING":
        saving, saving_display = calc_saving(rid, "p_cvm", "cvm_scheduled_off", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "非生产可定时关机",
            "suggestion": "建议配置定时关机（夜间/周末停机，预计省57%）",
            "target_spec": "配置定时关机策略",
            "severity": "low",
            "risk": "[低] 可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })

    return findings


def judge_cbs(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CBS 判定（analysis-rules.md §5.4）：未挂载 + 存储类型降级。"""
    rid = inst.get("DiskId", "")
    disk_type = inst.get("DiskType", "")
    findings = []

    # 路径1: 未挂载 → 刊例价月省（F41）
    if inst.get("DiskState") == "UNATTACHED":
        saving, saving_display = calc_saving_cbs(inst)
        return [{
            "issue_type": "未挂载",
            "suggestion": "快照备份后删除",
            "target_spec": "释放磁盘",
            "severity": "critical",
            "risk": "[严重] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        }]

    # 路径2: SSD 低 IO → 可降级为高效云盘
    ssd_types = {"CLOUD_SSD", "CLOUD_BSSD", "CLOUD_TSSD", "CLOUD_HSSD"}
    if disk_type in ssd_types:
        io_pts = (mon.get("DiskReadIOPS") or mon.get("DiskWriteIOPS") or {}).get("points", [])
        # 合并读写 IOPS
        read_pts = (mon.get("DiskReadIOPS") or {}).get("points", [])
        write_pts = (mon.get("DiskWriteIOPS") or {}).get("points", [])
        if read_pts or write_pts:
            cov = multi_coverage({"read": read_pts, "write": write_pts})
            if cov >= 0.5:
                read_avg = safe_avg(read_pts) or 0
                write_avg = safe_avg(write_pts) or 0
                total_iops = read_avg + write_avg
                if total_iops < 100:
                    saving, saving_display = calc_saving(rid, "p_cbs", "cbs_type_downgrade", lookup, bill_summary, disc_count)
                    findings.append({
                        "issue_type": "SSD低IO可降级",
                        "suggestion": f"降级为高效云盘（当前{disk_type}，IO均值{int(total_iops)}IOPS<100）",
                        "severity": "medium",
                        "risk": "[中] 可逆 · 即时",
                        "saving": saving,
                        "saving_display": saving_display,
                        "coverage": round(cov, 3),
                        "coverage_note": coverage_note(cov),
                    })

    # 统一补充 target_spec
    for f in findings:
        it = f.get("issue_type", "")
        if it == "未挂载":
            f["target_spec"] = "释放磁盘"
        elif it == "SSD低IO可降级":
            f["target_spec"] = "转高效云盘"
        else:
            f["target_spec"] = "-"
    return findings


def judge_mysql(inst, mon, lookup, bill_summary, head_map, disc_count):
    """MySQL 闲置判定（analysis-rules.md §5.3）：闲置 + 降配 + 按量转包。"""
    cpu_pts = (mon.get("CPUUseRate") or {}).get("points", [])
    conn_pts = (mon.get("ConnectionUseRate") or {}).get("points", [])
    mem_pts = (mon.get("MemoryUseRate") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "conn": conn_pts, "mem": mem_pts})
    cov_note = coverage_note(cov)

    cpu_avg = safe_avg(cpu_pts)
    conn_avg = safe_avg(conn_pts)
    mem_avg = safe_avg(mem_pts)

    findings = []
    rid = inst.get("InstanceId", "")
    # 腾讯云 MySQL PayType: 0=包年包月(PREPAID), 1=按量计费(POSTPAID)
    # ChargeType 字段可能为空，优先读 PayType
    raw_charge = inst.get("ChargeType", inst.get("PayType", ""))
    if raw_charge in (0, "0"):
        charge = "PREPAID"
    elif raw_charge in (1, "1"):
        charge = "POSTPAID"
    else:
        charge = str(raw_charge)

    # 提前计算规格信息（用于降配建议）
    cpu_cores = inst.get("Cpu", 0) or 0
    mem_gb = _mysql_mem_gb(inst.get("Memory", 0))
    spec_str = f"{cpu_cores}核{mem_gb}GB"

    # 路径1: 闲置：CPU<5% AND 连接<5% AND 内存<20%
    if cov >= 0.5:
        if (cpu_avg is not None and cpu_avg < 5 and
                conn_avg is not None and conn_avg < 5 and
                mem_avg is not None and mem_avg < 20):
            saving, saving_display = calc_saving(rid, "p_cdb", "mysql_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "闲置",
                "suggestion": "关停或释放（请评审）",
                "target_spec": "释放实例",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })
            return findings

        # 路径2: 可降配：CPU<5% AND 连接<20% AND 内存<30%
        if (cpu_avg is not None and cpu_avg < 5 and
                conn_avg is not None and conn_avg < 20 and
                mem_avg is not None and mem_avg < 30):
            target_spec = _mysql_target_spec(cpu_cores, mem_gb, "低利用率可降配")
            # 从 target_spec 提取目标规格（"降至 2核4GB" → "2核4GB"）
            target_spec_short = target_spec.replace("降至 ", "") if target_spec.startswith("降至") else target_spec
            suggestion = f"{spec_str} → {target_spec_short}" if target_spec != "-" else "降配至更小规格"
            saving, saving_display = calc_saving(rid, "p_cdb", "mysql_downsize", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "低利用率可降配",
                "suggestion": suggestion,
                "target_spec": target_spec,
                "severity": "high",
                "risk": "[高] 可逆 · 业务低峰",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    # 路径3: 按量转包（与利用率无关）
    if charge in ("POSTPAID", "POSTPAID_BY_HOUR"):
        created = inst.get("CreateTime", inst.get("CreatedTime", ""))
        try:
            from datetime import datetime, timezone
            ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - ct).days
        except Exception:
            age_days = 999
        if age_days >= 30:
            saving, saving_display = calc_saving(rid, "p_cdb", "mysql_to_annual", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "按量长期运行",
                "suggestion": "转包年包月（节省约40%）",
                "target_spec": "转包年包月",
                "severity": "critical",
                "risk": "[严重] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": None,
                "coverage_note": "",
            })

    return findings


def judge_redis(inst, mon, lookup, bill_summary, head_map, disc_count):
    """Redis 判定（analysis-rules.md §5.3）：闲置 + 降配。"""
    mem_pts = (mon.get("MemUsed") or {}).get("points", [])
    conn_pts = (mon.get("Connections") or {}).get("points", [])

    cov = multi_coverage({"mem": mem_pts, "conn": conn_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    rid = inst.get("InstanceId", "")
    size_mb = inst.get("Size", 0) or 0  # Redis Size 单位 MB

    mem_vals = [float(v) for v in (mem_pts or []) if v is not None]
    mem_used_avg = sum(mem_vals) / len(mem_vals) if mem_vals else None
    mem_pct = (mem_used_avg / size_mb * 100) if (mem_used_avg is not None and size_mb > 0) else None

    # 内存峰值用于降配建议
    mem_used_max = max(mem_vals) if mem_vals else None
    mem_max_pct = (mem_used_max / size_mb * 100) if (mem_used_max is not None and size_mb > 0) else None

    conn_avg = safe_avg(conn_pts)
    findings = []

    # 路径1: 闲置：内存<10% AND 连接<5
    if (mem_pct is not None and mem_pct < 10 and
            conn_avg is not None and conn_avg < 5):
        saving, saving_display = calc_saving(rid, "p_redis", "redis_release", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "闲置",
            "suggestion": "关停或释放（请评审）",
            "severity": "high",
            "risk": "[高] 不可逆 · 维护窗口",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        })
        return findings

    # 路径2: 降配：按内存峰值×2取档位
    if mem_max_pct is not None and mem_max_pct < 50 and size_mb > 0:
        target_mb = mem_used_max * 2 if mem_used_max else size_mb * 0.2
        # 向上取最近档位
        tiers = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
        target_mb = min((t for t in tiers if t >= target_mb), default=size_mb)
        if target_mb < size_mb:
            size_gb = size_mb / 1024 if size_mb >= 1024 else size_mb
            target_gb = target_mb / 1024 if target_mb >= 1024 else target_mb
            saving, saving_display = calc_saving(rid, "p_redis", "redis_downsize", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "内存低可降配",
                "suggestion": f"{size_gb}GB → {target_gb}GB（峰值用量{int(mem_max_pct)}%，建议峰值×2）",
                "target_spec": f"降至{target_gb}GB",
                "severity": "high",
                "risk": "[高] 可逆 · 业务低峰",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    # 闲置的 target_spec 已内联
    for f in findings:
        if "target_spec" not in f:
            it = f.get("issue_type", "")
            if it == "闲置":
                f["target_spec"] = "释放实例"
            else:
                f["target_spec"] = "-"
    return findings


def _mongodb_spec_str(inst):
    """从 MongoDB 实例中提取规格字符串，如 '8核16GB'。"""
    cpu = inst.get("CpuNum", 0) or 0
    mem_mb = inst.get("Memory", 0) or 0
    mem_gb = int(mem_mb / 1024) if mem_mb > 256 else mem_mb
    return cpu, mem_gb, f"{cpu}核{mem_gb}GB"


def _mongodb_target_spec(cpu, mem_gb):
    """根据 MongoDB spec_tiers 计算降配目标。"""
    tiers = [
        (1, 2), (2, 4), (4, 8), (4, 16), (8, 16),
        (8, 32), (16, 64), (32, 64), (64, 128), (96, 256),
    ]
    current_idx = None
    for i, (tc, tm) in enumerate(tiers):
        if tc == cpu and tm == mem_gb:
            current_idx = i
            break
    if current_idx is None:
        for i, (tc, tm) in enumerate(tiers):
            if tc >= cpu and tm >= mem_gb:
                current_idx = i
                break
    if current_idx is not None and current_idx > 0:
        tc, tm = tiers[current_idx - 1]
        return f"降至 {tc}核{tm}GB"
    if current_idx == 0:
        return "已达最小规格"
    return "降配"


def judge_mongodb(inst, mon, lookup, bill_summary, head_map, disc_count):
    """MongoDB 闲置/降配/按量转包 判定（CPU + QPS + 连接数）。"""
    cpu_pts = (mon.get("MonogdAvgCpuUsage") or {}).get("points", [])
    qps_pts = (mon.get("Qps") or {}).get("points", [])
    conn_pts = (mon.get("ClusterConn") or {}).get("points", [])

    # ClusterConn 可能 unauthorized，降级只用 CPU+QPS
    metrics = {"cpu": cpu_pts, "qps": qps_pts}
    if conn_pts:
        metrics["conn"] = conn_pts
    cov = multi_coverage(metrics)
    cov_note = coverage_note(cov)

    rid = inst.get("InstanceId", "")
    cpu_avg = safe_avg(cpu_pts)
    qps_avg = safe_avg(qps_pts)
    conn_avg = safe_avg(conn_pts) if conn_pts else None

    # 提前计算规格信息
    cpu_cores, mem_gb, spec_str = _mongodb_spec_str(inst)

    findings = []

    if cov >= 0.5:
        # 三指标联合（ClusterConn 缺失时二指标）
        cpu_ok = cpu_avg is not None and cpu_avg < 5
        qps_ok = qps_avg is not None and qps_avg < 1
        conn_ok = conn_avg is None or conn_avg < 10  # 缺失时放宽

        # 路径1: 闲置
        if cpu_ok and qps_ok and conn_ok:
            saving, saving_display = calc_saving(rid, "p_mongodb", "mongo_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "闲置",
                "suggestion": "关停或释放（请评审）",
                "target_spec": "释放实例",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note + ("（ClusterConn 数据缺失，仅 CPU+QPS 判定）" if not conn_pts else ""),
            })
            return findings

        # 路径2: 低利用率可降配
        cpu_low = cpu_avg is not None and cpu_avg < 10
        qps_low = qps_avg is not None and qps_avg < 10
        if cpu_low and qps_low:
            target_spec = _mongodb_target_spec(cpu_cores, mem_gb)
            if target_spec == "已达最小规格":
                suggestion = f"{spec_str}（已达最小规格，建议评估是否释放）"
            elif target_spec != "降配":
                target_short = target_spec.replace("降至 ", "")
                suggestion = f"{spec_str} → {target_short}"
            else:
                suggestion = "降配至更小规格"
            saving, saving_display = calc_saving(rid, "p_mongodb", "mongo_downsize", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "低利用率可降配",
                "suggestion": suggestion,
                "target_spec": target_spec,
                "severity": "high",
                "risk": "[高] 可逆 · 业务低峰",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    # 路径3: 按量转包
    raw_charge = inst.get("PayMode", inst.get("ChargeType", ""))
    if raw_charge in (0, "0"):
        charge = "PREPAID"
    elif raw_charge in (1, "1"):
        charge = "POSTPAID"
    else:
        charge = str(raw_charge) if raw_charge else ""
    if charge == "POSTPAID":
        age_days = _compute_age_days(inst)
        if age_days is not None and age_days >= 30:
            saving, saving_display = calc_saving(rid, "p_mongodb", "mongo_to_annual", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "按量长期运行",
                "suggestion": "转包年包月（节省约40%）",
                "target_spec": "转包年包月",
                "severity": "critical",
                "risk": "[严重] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": None,
                "coverage_note": "",
            })

    for f in findings:
        if "target_spec" not in f:
            f["target_spec"] = "-"
    return findings


def judge_pgsql(inst, mon, lookup, bill_summary, head_map, disc_count):
    """PostgreSQL 闲置判定（CPU + 内存率 + 连接数）。"""
    cpu_pts = (mon.get("Cpu") or {}).get("points", [])
    mem_pts = (mon.get("MemoryRate") or {}).get("points", [])
    conn_pts = (mon.get("Connections") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "mem": mem_pts, "conn": conn_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    rid = inst.get("DBInstanceId", inst.get("InstanceId", ""))
    cpu_avg = safe_avg(cpu_pts)
    mem_avg = safe_avg(mem_pts)
    conn_avg = safe_avg(conn_pts)
    findings = []

    if (cpu_avg is not None and cpu_avg < 5 and
            mem_avg is not None and mem_avg < 20 and
            conn_avg is not None and conn_avg < 5):
        saving, saving_display = calc_saving(rid, "p_postgres", "pgsql_release", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "闲置",
            "suggestion": "关停或释放（请评审）",
            "severity": "high",
            "risk": "[高] 不可逆 · 维护窗口",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        })

    for f in findings:
        f["target_spec"] = "释放实例" if f.get("issue_type") == "闲置" else "-"
    return findings


def judge_clb(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CLB 判定（analysis-rules.md §5.5）：无后端绑定 + 有后端但无连接。

    DescribeTargets 返回结构：
      Listeners[].Targets          — 四层监听器（TCP/UDP/QUIC）的后端列表
      Listeners[].Rules[].Targets  — 七层监听器（HTTP/HTTPS/TCP SSL）的后端列表
    两者都需检查，任意一个有绑定即视为有后端。
    """
    listeners = inst.get("Targets") or []  # prepare 脚本把 resp["Listeners"] 存入 inst["Targets"]
    rid = inst.get("LoadBalancerId", "")
    findings = []

    # 判断是否有后端（同时检查四层 Targets 和七层 Rules[].Targets）
    has_backend = False
    has_listener = False  # 是否配置了监听器
    for listener in listeners:
        # 四层监听器（TCP/UDP/QUIC）：后端在 listener["Targets"]
        layer4_targets = listener.get("Targets") or []
        if layer4_targets:
            has_backend = True
        # 七层监听器（HTTP/HTTPS/TCP SSL）：后端在 listener["Rules"][*]["Targets"]
        for rule in (listener.get("Rules") or []):
            rule_targets = rule.get("Targets") or []
            if rule_targets:
                has_backend = True
        # 检查是否有监听器配置（Protocol/Port 存在即视为有监听器）
        if listener.get("ListenerId") or listener.get("Protocol"):
            has_listener = True

    # 路径1: 无后端绑定 → 严重（纯浪费，可即时释放）
    if not has_backend:
        if not has_listener:
            issue = "无监听器"
            suggestion = "确认后释放（未配置任何监听器，纯浪费）"
        else:
            issue = "无后端绑定"
            suggestion = "确认后释放（有监听器但无 RS 绑定，纯浪费）"
        saving, saving_display = calc_saving(rid, "p_clb", "clb_release", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": issue,
            "suggestion": suggestion,
            "severity": "critical",
            "risk": "[严重] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })
        return findings

    # 路径2: 有后端但无连接（监控判定）
    conn_pts = (mon.get("ConNum") or mon.get("ClientConnum") or {}).get("points", [])
    if conn_pts:
        cov = coverage_rate(conn_pts)
        conn_avg = safe_avg(conn_pts)
        if cov >= 0.5 and conn_avg is not None and conn_avg < 1:
            saving, saving_display = calc_saving(rid, "p_clb", "clb_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "有后端但无连接",
                "suggestion": "确认业务是否停用后释放",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": coverage_note(cov),
            })

    # 路径3: 有后端有连接但连接数极低
    elif conn_pts:
        cov = coverage_rate(conn_pts)
        conn_avg = safe_avg(conn_pts)
        if cov >= 0.5 and conn_avg is not None and 1 <= conn_avg < 10:
            saving, saving_display = calc_saving(rid, "p_clb", "clb_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "连接数极低",
                "suggestion": "确认业务是否可合并至其他 CLB",
                "severity": "medium",
                "risk": "[中] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": coverage_note(cov),
            })

    for f in findings:
        it = f.get("issue_type", "")
        if it in ("无监听器", "无后端绑定"):
            f["target_spec"] = "释放实例"
        elif it in ("有后端但无连接", "连接数极低"):
            f["target_spec"] = "确认停用后释放"
        else:
            f["target_spec"] = "-"
    return findings


def judge_eip(inst, mon, lookup, bill_summary, head_map, disc_count):
    """EIP：未绑定判定。"""
    if inst.get("AddressStatus") == "UNBIND":
        rid = inst.get("AddressId", "")
        saving, saving_display = calc_saving(rid, "p_eip", "eip_release", lookup, bill_summary, disc_count)
        return [{
            "issue_type": "未绑定",
            "suggestion": "释放回收",
            "target_spec": "释放EIP",
            "severity": "high",
            "risk": "[高] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        }]
    return []


def judge_nat(inst, mon, lookup, bill_summary, head_map, disc_count):
    """NAT：出带宽均值 < 购买带宽 5%。"""
    out_pts = (mon.get("OutBandwidth") or {}).get("points", [])
    cov = coverage_rate(out_pts)
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    out_avg = safe_avg(out_pts)
    # BandwidthOutMbps 字段（Mbps）
    purchased_bw = inst.get("BandwidthOutMbps") or inst.get("MaxBandWidthOut") or 0

    findings = []
    if out_avg is not None and purchased_bw > 0:
        if out_avg < purchased_bw * 0.05:
            rid = inst.get("NatGatewayId", "")
            saving, saving_display = calc_saving(rid, "p_nat", "nat_downsize", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "低利用率",
                "suggestion": "降带宽配额（长期合同，仅供参考）",
                "target_spec": f"降至 {max(1, int(purchased_bw * 0.5))}Mbps",
                "severity": "medium",
                "risk": "[中] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })
    for f in findings:
        if "target_spec" not in f:
            f["target_spec"] = "-"
    return findings


def judge_cfs(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CFS 判定（analysis-rules.md §5.7）：无连接 + 近无IO + 存储类型降级。"""
    read_pts = (mon.get("DataReadIoBytes") or {}).get("points", [])
    write_pts = (mon.get("DataWriteIoBytes") or {}).get("points", [])
    client_pts = (mon.get("ClientCount") or {}).get("points", [])

    rid = inst.get("FileSystemId", "")
    findings = []

    # 路径1: 无客户端连接（无论 IO 高低，没连接=没人用）
    if client_pts:
        cov_client = coverage_rate(client_pts)
        client_avg = safe_avg(client_pts)
        if cov_client >= 0.5 and client_avg is not None and client_avg < 1:
            saving, saving_display = calc_saving(rid, "p_cfs", "cfs_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "无客户端连接",
                "suggestion": "确认后释放",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov_client, 3),
                "coverage_note": coverage_note(cov_client),
            })
            return findings

    # 路径2: 近无IO
    cov = multi_coverage({"read": read_pts, "write": write_pts})
    cov_note = coverage_note(cov)

    if cov >= 0.5:
        read_avg = safe_avg(read_pts)
        write_avg = safe_avg(write_pts)
        client_avg = safe_avg(client_pts) if client_pts else None

        if (read_avg is not None and read_avg < 0.01 and
                write_avg is not None and write_avg < 0.01):
            saving, saving_display = calc_saving(rid, "p_cfs", "cfs_release", lookup, bill_summary, disc_count)
            note = cov_note
            if client_avg is not None and client_avg > 0:
                note += " 挂载但无 IO，疑似低活跃"
            findings.append({
                "issue_type": "近无IO",
                "suggestion": "确认后释放",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": note,
            })
            return findings

        # 路径3: 性能型低IO → 可降为标准型
        fs_type = inst.get("FsType", inst.get("Protocol", ""))
        if "性能" in str(fs_type) or str(fs_type).upper() == "PERFORMANCE":
            io_avg = (read_avg or 0) + (write_avg or 0)
            if io_avg < 1:  # 低IO，性能型没必要
                saving, saving_display = calc_saving(rid, "p_cfs", "cfs_type_downgrade", lookup, bill_summary, disc_count)
                findings.append({
                    "issue_type": "性能型低IO可降级",
                    "suggestion": "转标准型/Turbo标准型（当前性能型但IO均值极低）",
                    "severity": "medium",
                    "risk": "[中] 可逆 · 即时",
                    "saving": saving,
                    "saving_display": saving_display,
                    "coverage": round(cov, 3),
                    "coverage_note": cov_note,
                })

    for f in findings:
        it = f.get("issue_type", "")
        if it in ("无客户端连接", "近无IO"):
            f["target_spec"] = "释放实例"
        elif it == "性能型低IO可降级":
            f["target_spec"] = "转标准型/Turbo标准型"
        else:
            f["target_spec"] = "-"
    return findings


def judge_es(inst, mon, lookup, bill_summary, head_map, disc_count):
    """ES：CPU + JVM内存均值判定。"""
    cpu_pts = (mon.get("CpuUsageAvg") or {}).get("points", [])
    jvm_pts = (mon.get("JvmMemUsageAvg") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "jvm": jvm_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    cpu_avg = safe_avg(cpu_pts)
    jvm_avg = safe_avg(jvm_pts)
    rid = inst.get("InstanceId", "")
    findings = []

    # 闲置：CPU<5% AND JVM内存<20%
    if cpu_avg is not None and cpu_avg < 5 and jvm_avg is not None and jvm_avg < 20:
        saving, saving_display = calc_saving(rid, "p_es", "es_release", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "闲置",
            "suggestion": "释放或降配",
            "severity": "medium",
            "risk": "[中] 不可逆 · 维护窗口",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        })
        return findings

    # 低利用率：CPU<15% AND JVM<50%
    if cpu_avg is not None and cpu_avg < 15 and jvm_avg is not None and jvm_avg < 50:
        saving, saving_display = calc_saving(rid, "p_es", "es_downsize", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "低利用率可降配",
            "suggestion": "缩节点或降配",
            "severity": "medium",
            "risk": "[中] 可逆 · 业务低峰",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        })

    for f in findings:
        it = f.get("issue_type", "")
        if it == "闲置":
            f["target_spec"] = "释放实例"
        elif it == "低利用率可降配":
            f["target_spec"] = "缩节点或降配"
        else:
            f["target_spec"] = "-"
    return findings


def judge_ckafka(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CKafka：生产+消费流量均低判定。"""
    pro_pts = (mon.get("InstanceProFlow") or {}).get("points", [])
    con_pts = (mon.get("InstanceConFlow") or {}).get("points", [])

    cov = multi_coverage({"pro": pro_pts, "con": con_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    pro_avg = safe_avg(pro_pts)
    con_avg = safe_avg(con_pts)
    rid = inst.get("InstanceId", "")

    if pro_avg is not None and pro_avg < 0.1 and con_avg is not None and con_avg < 0.1:
        saving, saving_display = calc_saving(rid, "p_ckafka", "ckafka_release", lookup, bill_summary, disc_count)
        return [{
            "issue_type": "闲置",
            "suggestion": "确认后释放",
            "target_spec": "释放实例",
            "severity": "medium",
            "risk": "[中] 不可逆 · 维护窗口",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        }]
    return []


def judge_tke(inst, mon, lookup, bill_summary, head_map, disc_count):
    """TKE：空集群或低利用率集群。"""
    rid = inst.get("ClusterId", inst.get("InstanceId", ""))
    node_num = inst.get("ClusterNodeNum", inst.get("WorkerInstancesSize", 0)) or 0
    status = inst.get("ClusterStatus", "")

    # 空集群
    if node_num == 0 and status == "Running":
        saving, saving_display = calc_saving(rid, "p_tke", "tke_release", lookup, bill_summary, disc_count)
        return [{
            "issue_type": "空集群",
            "suggestion": "确认后销毁集群",
            "target_spec": "销毁集群",
            "severity": "low",
            "risk": "[低] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        }]

    # 低利用率
    cpu_pts = (mon.get("K8sClusterRateCpuCoreUsedCluster") or {}).get("points", [])
    mem_pts = (mon.get("K8sClusterRateMemUsageBytesCluster") or {}).get("points", [])

    if not cpu_pts or not mem_pts:
        return []

    cov = multi_coverage({"cpu": cpu_pts, "mem": mem_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    cpu_avg = safe_avg(cpu_pts)
    mem_avg = safe_avg(mem_pts)

    if cpu_avg is not None and cpu_avg < 15 and mem_avg is not None and mem_avg < 30:
        saving, saving_display = calc_saving(rid, "p_tke", "tke_downsize", lookup, bill_summary, disc_count)
        return [{
            "issue_type": "低利用率",
            "suggestion": "缩减节点池规模 / 启用 ClusterAutoscaler",
            "target_spec": "缩减节点池 / 启用 CA",
            "severity": "medium",
            "risk": "[中] 可逆 · 业务低峰",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        }]

    return []


def judge_cos(inst, mon, lookup, bill_summary, head_map, disc_count):
    """COS：低访问频率存储类型优化。"""
    bucket = inst.get("Bucket") or inst.get("ResourceId", "")
    get_req_pts = (mon.get("GetRequests") or {}).get("points", [])
    std_storage_pts = (mon.get("StdStorage") or {}).get("points", [])

    get_req_avg = safe_avg(get_req_pts)
    std_storage_avg = safe_avg(std_storage_pts)

    if std_storage_avg is None or std_storage_avg == 0:
        return []  # 没有标准存储，跳过

    # 转低频（GetRequests<10/天，StdStorage>100GB）
    get_per_day = (get_req_avg or 0) * 24  # 监控点每小时
    findings = []

    if get_per_day < 1 and std_storage_avg > 100:
        saving, saving_display = calc_saving(bucket, "p_cos", "cos_archive", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "极低访问频率",
            "suggestion": "配置生命周期转归档存储（省72%）",
            "severity": "low",
            "risk": "[低] 可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })
    elif get_per_day < 10 and std_storage_avg > 100:
        saving, saving_display = calc_saving(bucket, "p_cos", "cos_infrequent", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "低访问频率",
            "suggestion": "配置生命周期转低频存储（省32%）",
            "severity": "low",
            "risk": "[低] 可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })

    for f in findings:
        it = f.get("issue_type", "")
        if it == "极低访问频率":
            f["target_spec"] = "转归档存储"
        elif it == "低访问频率":
            f["target_spec"] = "转低频存储"
        else:
            f["target_spec"] = "-"
    return findings


def judge_vectordb(inst, mon, lookup, bill_summary, head_map, disc_count):
    """VectorDB 闲置判定。"""
    cpu_pts = (mon.get("ClusterNodesAvgCpuUtil") or {}).get("points", [])
    mem_pts = (mon.get("ClusterNodesAvgMemUtil") or {}).get("points", [])
    disk_pts = (mon.get("ClusterNodesAvgDiskUsageUtil") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "mem": mem_pts, "disk": disk_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    cpu_avg = safe_avg(cpu_pts)
    mem_avg = safe_avg(mem_pts)
    disk_avg = safe_avg(disk_pts)
    rid = inst.get("InstanceId", "")

    if (cpu_avg is not None and cpu_avg < 5 and
            mem_avg is not None and mem_avg < 20 and
            disk_avg is not None and disk_avg < 10):
        saving, saving_display = calc_saving(rid, "p_vectordb", "vecdb_release", lookup, bill_summary, disc_count)
        return [{
            "issue_type": "闲置",
            "suggestion": "释放或降规格（请评审）",
            "target_spec": "释放实例",
            "severity": "high",
            "risk": "[高] 不可逆 · 维护窗口",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        }]
    return []


def judge_vpn(inst, mon, lookup, bill_summary, head_map, disc_count):
    """VPN 网关：低利用率判定（analysis-rules.md §5.5）。"""
    out_pts = (mon.get("VpnOutbandwidth") or mon.get("OutBandwidth") or {}).get("points", [])
    cov = coverage_rate(out_pts)
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    out_avg = safe_avg(out_pts)
    rid = inst.get("VpnGatewayId", "")
    findings = []

    if out_avg is not None and out_avg < 0.01:  # 带宽几乎为 0
        saving, saving_display = calc_saving(rid, "p_vpn", "vpn_downsize", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "低利用率",
            "suggestion": "确认业务后释放或降配（长期合同，仅供参考）",
            "severity": "medium",
            "risk": "[中] 可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": round(cov, 3),
            "coverage_note": cov_note,
        })

    for f in findings:
        f["target_spec"] = "释放或降配" if f.get("issue_type") == "低利用率" else "-"
    return findings


def judge_emr(inst, mon, lookup, bill_summary, head_map, disc_count):
    """EMR 判定（analysis-rules.md §5.6）：已终止仍计费 + 低利用率。"""
    rid = inst.get("InstanceId", inst.get("Id", ""))
    status = str(inst.get("Status", ""))
    findings = []

    # 路径1: 已终止仍计费
    if status in ("terminated_with_prepaid", "9", "terminated"):
        saving, saving_display = calc_saving(rid, "p_emr", "emr_release", lookup, bill_summary, disc_count)
        findings.append({
            "issue_type": "已终止仍计费",
            "suggestion": "退订或释放（包年包月到期前仍在计费）",
            "target_spec": "退订释放",
            "severity": "high",
            "risk": "[高] 不可逆 · 即时",
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
        })
        return findings

    # 路径2: 低利用率
    cpu_pts = (mon.get("CpuUsage") or mon.get("CPUUsage") or {}).get("points", [])
    if cpu_pts:
        cov = coverage_rate(cpu_pts)
        if cov >= 0.5:
            cpu_avg = safe_avg(cpu_pts)
            if cpu_avg is not None and cpu_avg < 5:
                saving, saving_display = calc_saving(rid, "p_emr", "emr_downsize", lookup, bill_summary, disc_count)
                findings.append({
                    "issue_type": "低利用率",
                    "suggestion": "降配或缩节点",
                    "target_spec": "降配或缩节点",
                    "severity": "medium",
                    "risk": "[中] 可逆 · 业务低峰",
                    "saving": saving,
                    "saving_display": saving_display,
                    "coverage": round(cov, 3),
                    "coverage_note": coverage_note(cov),
                })

    for f in findings:
        if "target_spec" not in f:
            f["target_spec"] = "-"
    return findings


def judge_dc(inst, mon, lookup, bill_summary, head_map, disc_count):
    """DC 专线：带宽利用率低判定（analysis-rules.md §5.8）。"""
    rid = inst.get("DirectConnectId", "")
    state = inst.get("State", "")
    if state != "AVAILABLE":
        return []

    in_pts = (mon.get("InBandwidth") or {}).get("points", [])
    out_pts = (mon.get("OutBandwidth") or {}).get("points", [])
    cov = multi_coverage({"in": in_pts, "out": out_pts})
    cov_note = coverage_note(cov)

    if cov < 0.5:
        return []

    in_avg = safe_avg(in_pts)
    out_avg = safe_avg(out_pts)
    purchased_bw = inst.get("Bandwidth", 0) or 0

    findings = []
    if purchased_bw > 0:
        if ((in_avg is None or in_avg < purchased_bw * 0.05) and
                (out_avg is None or out_avg < purchased_bw * 0.05)):
            saving, saving_display = calc_saving(rid, "p_directconnect", "dc_downsize", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "带宽利用率低",
                "suggestion": "联系业务确认是否可缩减（长期合同，仅供参考）",
                "severity": "medium",
                "risk": "[中] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    for f in findings:
        f["target_spec"] = "缩减带宽" if f.get("issue_type") == "带宽利用率低" else "-"
    return findings


def judge_cynosdb(inst, mon, lookup, bill_summary, head_map, disc_count):
    """CynosDB/TDSQL-C 判定：复用 MySQL 规则（analysis-rules.md §5.3 推测）。"""
    # 监控指标推测复用 MySQL
    cpu_pts = (mon.get("CPUUseRate") or mon.get("CpuUseRate") or {}).get("points", [])
    conn_pts = (mon.get("ConnectionUseRate") or {}).get("points", [])
    mem_pts = (mon.get("MemoryUseRate") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "conn": conn_pts, "mem": mem_pts})
    cov_note = coverage_note(cov)
    findings = []
    rid = inst.get("InstanceId", "")

    if cov >= 0.5:
        cpu_avg = safe_avg(cpu_pts)
        conn_avg = safe_avg(conn_pts)
        mem_avg = safe_avg(mem_pts)

        if (cpu_avg is not None and cpu_avg < 5 and
                conn_avg is not None and conn_avg < 5 and
                mem_avg is not None and mem_avg < 20):
            saving, saving_display = calc_saving(rid, "p_cynosdb", "mysql_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "闲置",
                "suggestion": "关停或释放（请评审）",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    # 按量转包
    raw_charge = inst.get("ChargeType", inst.get("PayType", ""))
    if raw_charge in (0, "0"):
        charge = "PREPAID"
    elif raw_charge in (1, "1"):
        charge = "POSTPAID"
    else:
        charge = str(raw_charge)
    if charge in ("POSTPAID", "POSTPAID_BY_HOUR"):
        created = inst.get("CreateTime", "")
        try:
            from datetime import datetime, timezone
            ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - ct).days
        except Exception:
            age_days = 999
        if age_days >= 30:
            saving, saving_display = calc_saving(rid, "p_cynosdb", "mysql_to_annual", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "按量长期运行",
                "suggestion": "转包年包月（节省约40%）",
                "severity": "critical",
                "risk": "[严重] 可逆 · 即时",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": None,
                "coverage_note": "",
            })

    for f in findings:
        it = f.get("issue_type", "")
        if it == "闲置":
            f["target_spec"] = "释放实例"
        elif it == "按量长期运行":
            f["target_spec"] = "转包年包月"
        else:
            f["target_spec"] = "-"
    return findings


def judge_mariadb(inst, mon, lookup, bill_summary, head_map, disc_count):
    """MariaDB 判定：复用 MySQL 规则（analysis-rules.md §5.3 推测）。"""
    cpu_pts = (mon.get("CpuUsageRate") or mon.get("CPUUseRate") or {}).get("points", [])
    mem_pts = (mon.get("MemUsageRate") or mon.get("MemoryUseRate") or {}).get("points", [])
    conn_pts = (mon.get("Connections") or {}).get("points", [])

    cov = multi_coverage({"cpu": cpu_pts, "mem": mem_pts, "conn": conn_pts})
    cov_note = coverage_note(cov)
    findings = []
    rid = inst.get("InstanceId", "")

    if cov >= 0.5:
        cpu_avg = safe_avg(cpu_pts)
        mem_avg = safe_avg(mem_pts)
        conn_avg = safe_avg(conn_pts)

        if (cpu_avg is not None and cpu_avg < 5 and
                mem_avg is not None and mem_avg < 20 and
                conn_avg is not None and conn_avg < 5):
            saving, saving_display = calc_saving(rid, "p_mariadb", "mysql_release", lookup, bill_summary, disc_count)
            findings.append({
                "issue_type": "闲置",
                "suggestion": "关停或释放（请评审）",
                "severity": "high",
                "risk": "[高] 不可逆 · 维护窗口",
                "saving": saving,
                "saving_display": saving_display,
                "coverage": round(cov, 3),
                "coverage_note": cov_note,
            })

    for f in findings:
        f["target_spec"] = "释放实例" if f.get("issue_type") == "闲置" else "-"
    return findings


# ─── 通用指标驱动判定引擎（Layer 2: 无需专用 judge_xxx() 也能分析） ────

# 通用指标名映射：产品API指标名 → 标准名
GENERIC_METRIC_ALIASES = {
    "cpu": ["CPUUseRate", "CpuUseRate", "CpuUsage", "MonogdAvgCpuUsage", "cpu_utilization"],
    "mem": ["MemoryUseRate", "MemUsage", "MemoryUsage", "mem_utilization"],
    "connections": ["ConnectionUseRate", "ClusterConn", "ConnUsageRate", "connections"],
    "qps": ["Qps", "QPS", "RequestsRate", "request_rate"],
    "bytes_in": ["InTraffic", "InBandwidth", "NetInRate"],
    "bytes_out": ["OutTraffic", "OutBandwidth", "NetOutRate"],
    "iops_read": ["ReadIOPS", "DiskReadIops"],
    "iops_write": ["WriteIOPS", "DiskWriteIops"],
}


def _resolve_metric(mon, aliases_by_std):
    """从监控数据中按别名列表尝试解析指标，返回 {标准名: avg值}。"""
    result = {}
    for std_name, aliases in aliases_by_std.items():
        for alias in aliases:
            pts = (mon.get(alias) or {}).get("points", [])
            if pts:
                avg = safe_avg(pts)
                if avg is not None:
                    result[std_name] = avg
                break
    return result


def _resolve_charge(inst, rules_config):
    """从实例信息中解析计费模式。"""
    charge_field = rules_config.get("charge_field", "")
    charge_mapping = rules_config.get("charge_mapping", {})
    if not charge_field:
        return None
    raw = inst.get(charge_field, "")
    if raw in charge_mapping:
        return charge_mapping[raw]
    if raw in ("POSTPAID", "POSTPAID_BY_HOUR"):
        return "POSTPAID"
    if raw in ("PREPAID",):
        return "PREPAID"
    return str(raw) if raw else None


def _compute_age_days(inst):
    """计算实例运行天数。"""
    created = inst.get("CreateTime", inst.get("CreatedTime", ""))
    if not created:
        return None
    try:
        ct = datetime.fromisoformat(created.replace("Z", "+00:00"))
        from datetime import timezone
        return (datetime.now(timezone.utc) - ct).days
    except Exception:
        return None


def _compute_downsize_target(inst, rules_config, std_metrics):
    """根据 spec_tiers 计算降配目标规格。"""
    spec_tiers = rules_config.get("spec_tiers", [])
    if not spec_tiers:
        return "-", "-"

    spec_fields = rules_config.get("spec_fields", [])
    # 尝试提取当前规格
    if len(spec_fields) >= 2:
        # 格式: [cpu_field, mem_field]
        raw_cpu = inst.get(spec_fields[0], 0) or 0
        raw_mem = inst.get(spec_fields[1], 0) or 0
        # 内存可能是 MB，需要转换
        mem_gb = raw_mem / 1024 if raw_mem > 256 else raw_mem
        cpu = int(raw_cpu) if isinstance(raw_cpu, (int, float)) else 0
    else:
        return "-", "-"

    # 在 spec_tiers 中查找当前规格的索引
    current_idx = None
    for i, tier in enumerate(spec_tiers):
        if len(tier) >= 2 and tier[0] == cpu and abs(tier[1] - mem_gb) < 1:
            current_idx = i
            break

    # 如果找不到精确匹配，找最接近的
    if current_idx is None:
        for i, tier in enumerate(spec_tiers):
            if len(tier) >= 2 and tier[0] >= cpu and tier[1] >= mem_gb:
                current_idx = i
                break

    if current_idx is not None and current_idx > 0:
        tc, tm = spec_tiers[current_idx - 1]
        target_spec = f"降至 {tc}核{int(tm)}GB"
        suggestion = f"{cpu}核{int(mem_gb)}GB → {tc}核{int(tm)}GB"
        return target_spec, suggestion
    elif current_idx == 0:
        return "已达最小规格", f"{cpu}核{int(mem_gb)}GB（已达最小规格，建议评估是否释放）"
    return "-", "-"


def generic_metric_judge(inst, mon, lookup, bill_summary, head_map, disc_count,
                         prod_title, product_code, rules_config):
    """通用指标驱动判定引擎。

    从 product_rules.yaml 读取规则，自动分析任意产品：
    1. 解析监控指标（通过别名映射）
    2. 逐条匹配规则条件
    3. 生成 finding（含 target_spec / optimize_path）
    4. 无匹配规则时输出到 unhandled_resources 供 LLM 分析
    """
    rid = (inst.get(rules_config.get("rid_field", "InstanceId"))
           or inst.get("InstanceId") or inst.get("ResourceId") or "")
    if not rid:
        return []

    # 1. 解析监控指标
    rules_metrics = rules_config.get("metrics", {})
    aliases_by_std = {}
    if rules_metrics:
        # 使用产品专属指标名映射
        for std_name, api_name in rules_metrics.items():
            aliases_by_std[std_name] = [api_name]
    else:
        # 使用通用别名映射
        aliases_by_std = GENERIC_METRIC_ALIASES

    std_metrics = _resolve_metric(mon, aliases_by_std)

    # 2. 解析计费模式和运行天数
    charge = _resolve_charge(inst, rules_config)
    age_days = _compute_age_days(inst)

    # 3. 逐条匹配规则
    rules = rules_config.get("rules", [])
    findings = []

    for rule in rules:
        rule_name = rule.get("name", "")
        condition = rule.get("condition", "")
        action = rule.get("action", "")
        severity = rule.get("severity", "medium")
        suggestion = rule.get("suggestion", "")
        target_spec = rule.get("target_spec", "-")
        optimize_path = rule.get("optimize_path", "")

        # 简化条件评估
        matched = _evaluate_condition(condition, std_metrics, inst, charge, age_days)
        if not matched:
            continue

        # 降配类规则需要计算具体目标规格
        if action == "downsize" and target_spec == "-":
            ts, sg = _compute_downsize_target(inst, rules_config, std_metrics)
            target_spec = ts
            if sg != "-":
                suggestion = sg

        # 节省金额计算
        saving_key = f"{product_code}_{rule_name}"
        if action == "release":
            saving_key = f"{product_code}_release"
        elif action == "downsize":
            saving_key = f"{product_code}_downsize"
        elif action == "switch_billing":
            saving_key = f"{product_code}_to_annual"
        saving, saving_display = calc_saving(rid, product_code, saving_key, lookup, bill_summary, disc_count)

        f = {
            "issue_type": rule_name,
            "suggestion": suggestion,
            "target_spec": target_spec,
            "severity": severity,
            "risk": SEVERITY_RISK_LABEL.get(severity, "[中]"),
            "saving": saving,
            "saving_display": saving_display,
            "coverage": None,
            "coverage_note": "",
            "optimize_path": optimize_path or _issue_to_optimize_path(rule_name),
        }
        findings.append(f)

    return findings


def _evaluate_condition(condition, std_metrics, inst, charge, age_days):
    """简化的条件评估器，支持 AND 组合的基本条件。

    支持的格式:
      - "cpu_avg < 5 AND mem_avg < 30"
      - "charge = POSTPAID AND age_days >= 30"
      - "state = STOPPED AND charge = POSTPAID"
      - "listeners_count = 0"
    """
    if not condition:
        return False

    parts = [p.strip() for p in condition.split(" AND ")]
    for part in parts:
        if not _eval_single(part, std_metrics, inst, charge, age_days):
            return False
    return True


def _eval_single(expr, std_metrics, inst, charge, age_days):
    """评估单个条件表达式。"""
    # 支持 =, <, >, <=, >=, != 操作符
    for op in (">=", "<=", "!=", ">", "<", "="):
        if op in expr:
            left, right = expr.split(op, 1)
            left = left.strip()
            right = right.strip()
            break
    else:
        return False

    # 解析左值
    left_val = _resolve_value(left, std_metrics, inst, charge, age_days)
    if left_val is None:
        # 缺失指标：如果条件是 < 某个值，缺失视为不满足
        return False

    # 解析右值
    try:
        if right.upper() in ("POSTPAID", "PREPAID", "NULL", "TRUE", "FALSE"):
            right_val = right.upper()
        elif right.replace(".", "", 1).isdigit():
            right_val = float(right)
        else:
            right_val = right
    except Exception:
        right_val = right

    # 类型统一
    if isinstance(left_val, str) and isinstance(right_val, str):
        pass
    elif isinstance(left_val, (int, float)) and isinstance(right_val, str):
        try:
            right_val = float(right_val)
        except ValueError:
            return left_val == right_val
    elif isinstance(right_val, (int, float)) and isinstance(left_val, str):
        try:
            left_val = float(left_val)
        except ValueError:
            return left_val == right_val

    # 比较
    if op == "=":
        return left_val == right_val
    elif op == ">":
        return left_val > right_val
    elif op == "<":
        return left_val < right_val
    elif op == ">=":
        return left_val >= right_val
    elif op == "<=":
        return left_val <= right_val
    elif op == "!=":
        return left_val != right_val
    return False


def _resolve_value(name, std_metrics, inst, charge, age_days):
    """解析条件左值的值。"""
    # 标准指标名
    suffix = "_avg"
    if name.endswith(suffix):
        metric_name = name[:-len(suffix)]
        if metric_name in std_metrics:
            return std_metrics[metric_name]

    # 实例属性
    if name == "charge":
        return charge
    if name == "age_days":
        return age_days
    if name == "state":
        return inst.get("InstanceState", inst.get("InstanceStatusDesc", ""))
    if name == "attached":
        return inst.get("Attachable", inst.get("Attached", ""))
    if name == "listeners_count":
        listeners = inst.get("Listeners", [])
        return len(listeners) if isinstance(listeners, list) else 0
    if name == "backends_count":
        targets = inst.get("Targets", [])
        return len(targets) if isinstance(targets, list) else 0
    if name == "env":
        return "nonprod"  # 简化：需要从标签判断

    # 尝试从实例属性直接读取
    val = inst.get(name)
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            return val
    return None


# ─── 产品 → 判定函数映射 ─────────────────────────────────────────

PRODUCT_JUDGE_MAP = {
    # title → (judge_fn, rid_field, name_field, spec_fn, product_code)
    "CVM 云服务器":       (judge_cvm, "InstanceId", "InstanceName", None, "p_cvm"),
    "CBS 云硬盘":         (judge_cbs, "DiskId", "DiskName", None, "p_cbs"),
    "MySQL":              (judge_mysql, "InstanceId", "InstanceName", None, "p_cdb"),
    "云数据库MySQL":      (judge_mysql, "InstanceId", "InstanceName", None, "p_cdb"),
    "Redis":              (judge_redis, "InstanceId", "InstanceName", None, "p_redis"),
    "云数据库Redis":      (judge_redis, "InstanceId", "InstanceName", None, "p_redis"),
    "MongoDB":            (judge_mongodb, "InstanceId", "InstanceName", None, "p_mongodb"),
    "云数据库MongoDB":    (judge_mongodb, "InstanceId", "InstanceName", None, "p_mongodb"),
    "PostgreSQL":         (judge_pgsql, "DBInstanceId", "DBInstanceName", None, "p_postgres"),
    "云数据库PostgreSQL": (judge_pgsql, "DBInstanceId", "DBInstanceName", None, "p_postgres"),
    "CLB 负载均衡":       (judge_clb, "LoadBalancerId", "LoadBalancerName", None, "p_clb"),
    "负载均衡CLB":        (judge_clb, "LoadBalancerId", "LoadBalancerName", None, "p_clb"),
    "EIP 弹性公网IP":     (judge_eip, "AddressId", "AddressName", None, "p_eip"),
    "弹性公网IP":         (judge_eip, "AddressId", "AddressName", None, "p_eip"),
    "NAT 网关":           (judge_nat, "NatGatewayId", "NatGatewayName", None, "p_nat"),
    "VPN 网关":           (judge_vpn, "VpnGatewayId", "VpnGatewayName", None, "p_vpn"),
    "CFS 文件存储":       (judge_cfs, "FileSystemId", "FsName", None, "p_cfs"),
    "ES Elasticsearch":   (judge_es, "InstanceId", "InstanceName", None, "p_es"),
    "CKafka 消息队列":    (judge_ckafka, "InstanceId", "InstanceName", None, "p_ckafka"),
    "TKE 容器服务":       (judge_tke, "ClusterId", "ClusterName", None, "p_tke"),
    "COS 对象存储":       (judge_cos, "ResourceId", "Bucket", None, "p_cos"),
    "向量数据库VectorDB": (judge_vectordb, "InstanceId", "InstanceName", None, "p_vectordb"),
    "EMR 弹性MapReduce":  (judge_emr, "Id", "ClusterName", None, "p_emr"),
    "DC 专线接入":        (judge_dc, "DirectConnectId", "DirectConnectName", None, "p_directconnect"),
    "CynosDB TDSQL-C":   (judge_cynosdb, "InstanceId", "InstanceName", None, "p_cynosdb"),
    "云数据库CynosDB":    (judge_cynosdb, "InstanceId", "InstanceName", None, "p_cynosdb"),
    "MariaDB":            (judge_mariadb, "InstanceId", "InstanceName", None, "p_mariadb"),
    "云数据库MariaDB":    (judge_mariadb, "InstanceId", "InstanceName", None, "p_mariadb"),
}

# 产品码直接映射（resource_discovery.json 的 key 是产品码，不是中文节标题）
PRODUCT_CODE_MAP = {
    "p_cvm":       "CVM 云服务器",
    "p_cbs":       "CBS 云硬盘",
    "p_cdb":       "MySQL",
    "p_redis":     "Redis",
    "p_mongodb":   "MongoDB",
    "p_postgresql":"PostgreSQL",
    "p_clb":       "CLB 负载均衡",
    "p_eip":       "EIP 弹性公网IP",
    "p_nat":       "NAT 网关",
    "p_vpn":       "VPN 网关",
    "p_cfs":       "CFS 文件存储",
    "p_es":        "ES Elasticsearch",
    "p_ckafka":    "CKafka 消息队列",
    "p_tke":       "TKE 容器服务",
    "p_cos":       "COS 对象存储",
    "p_vectormind":"向量数据库VectorDB",
    "p_vectordb":  "向量数据库VectorDB",
    "p_svh":       "轻量应用服务器",   # LH 轻量应用服务器
    "p_lmp":       "轻量应用服务器",   # LH 轻量应用服务器（另一产品码）
    "p_emr":       "EMR 弹性MapReduce",
    "p_directconnect": "DC 专线接入",
    "p_cynosdb":   "CynosDB TDSQL-C",
    "p_mariadb":   "MariaDB",
    "p_dcdb":      "MySQL",            # TDSQL 降级复用 MySQL
}

# monitor_summary.by_product 的短 key → 产品码映射
MONITOR_PROD_KEY = {
    "CVM 云服务器":       "cvm",
    "CBS 云硬盘":         "cbs",
    "MySQL":              "cdb",
    "云数据库MySQL":      "cdb",
    "Redis":              "redis",
    "云数据库Redis":      "redis",
    "MongoDB":            "mongodb",
    "云数据库MongoDB":    "mongodb",
    "PostgreSQL":         "postgresql",
    "云数据库PostgreSQL": "postgresql",
    "CLB 负载均衡":       "clb",
    "负载均衡CLB":        "clb",
    "EIP 弹性公网IP":     "eip",
    "弹性公网IP":         "eip",
    "NAT 网关":           "nat",
    "CFS 文件存储":       "cfs",
    "ES Elasticsearch":   "es",
    "CKafka 消息队列":    "ckafka",
    "TKE 容器服务":       "tke",
    "COS 对象存储":       "cos",
    "向量数据库VectorDB": "vectordb",
    "VPN 网关":           "vpn",
    "EMR 弹性MapReduce":  "emr",
    "DC 专线接入":        "dc",
    "CynosDB TDSQL-C":   "cynosdb",
    "云数据库CynosDB":    "cynosdb",
    "MariaDB":            "mariadb",
    "云数据库MariaDB":    "mariadb",
}

# 别名映射（api-resource.md 中的节标题可能有变体）
PRODUCT_TITLE_ALIASES = {
    # 节标题变体
    "CVM":              "CVM 云服务器",
    "CBS":              "CBS 云硬盘",
    "MySQL 云数据库":   "云数据库MySQL",
    "Redis 云数据库":   "云数据库Redis",
    "MongoDB 云数据库": "云数据库MongoDB",
    "CLB":              "CLB 负载均衡",
    "EIP":              "EIP 弹性公网IP",
    "NAT":              "NAT 网关",
    "VPN":              "VPN 网关",
    "CFS":              "CFS 文件存储",
    "CFS（文件存储）":   "CFS 文件存储",
    "Elasticsearch":    "ES Elasticsearch",
    "CKafka":           "CKafka 消息队列",
    "TKE":              "TKE 容器服务",
    "COS":              "COS 对象存储",
    "VectorDB":         "向量数据库VectorDB",
    "EMR":              "EMR 弹性MapReduce",
    "DC":               "DC 专线接入",
    "CynosDB":          "CynosDB TDSQL-C",
    "TDSQL-C":          "CynosDB TDSQL-C",
    # disc 返回的括号格式别名
    "CCN 云联网":        "DC 专线接入",     # CCN 暂用 DC 的 judge
    "COS（对象存储）":   "COS 对象存储",
    "TKE（容器服务,托管集群）": "TKE 容器服务",
    "CKafka（消息队列）": "CKafka 消息队列",
    "DC（Direct Connect）": "DC 专线接入",
    "ES（Elasticsearch）": "ES Elasticsearch",
    "日志服务 CLS":       "COS 对象存储",     # CLS 暂无专用 judge，降级
    "密钥管理 KMS":       "COS 对象存储",     # KMS 暂无专用 judge，降级
    "TCHouse-D (原云数仓 Doris)": "EMR 弹性MapReduce",  # 降级
    "TDMQ Pulsar":       "CKafka 消息队列",  # 降级
    "NAT 网关":          "NAT 网关",
    "VPN 网关":          "VPN 网关",
    "CFS（文件存储）":   "CFS 文件存储",
    # advisor product_key 短名
    "cvm":              "CVM 云服务器",
    "cbs":              "CBS 云硬盘",
    "cdb":              "MySQL",
    "mysql":            "MySQL",
    "redis":            "Redis",
    "mongodb":          "MongoDB",
    "postgres":         "PostgreSQL",
    "postgresql":       "PostgreSQL",
    "clb":              "CLB 负载均衡",
    "eip":              "EIP 弹性公网IP",
    "nat":              "NAT 网关",
    "vpn":              "VPN 网关",
    "cfs":              "CFS 文件存储",
    "es":               "ES Elasticsearch",
    "ckafka":           "CKafka 消息队列",
    "tke":              "TKE 容器服务",
    "cos":              "COS 对象存储",
    "vectordb":         "向量数据库VectorDB",
    "Elasticsearch Service": "ES Elasticsearch",
    "emr":              "EMR 弹性MapReduce",
    "dc":               "DC 专线接入",
    "cynosdb":          "CynosDB TDSQL-C",
    "mariadb":          "MariaDB",
    "dcdb":             "MySQL",  # TDSQL 降级复用 MySQL
    # LH 轻量应用服务器
    "lh":               "轻量应用服务器",
    "轻量应用服务器":    "轻量应用服务器",
}


def resolve_product_title(title):
    """将 resource_discovery key（产品码或节标题）规范化为 PRODUCT_JUDGE_MAP key。
    支持三种格式：
    - 纯产品码: "p_cvm"
    - 纯节标题: "CVM 云服务器"
    - 复合格式: "CVM 云服务器|p_cvm"（prepare_costopt_data.py 旧版格式）
    """
    if title in PRODUCT_JUDGE_MAP:
        return title
    # 复合格式 "title|p_code" → 先拆分，分别尝试
    if "|" in title:
        parts = title.split("|", 1)
        for part in parts:
            part = part.strip()
            if part in PRODUCT_JUDGE_MAP:
                return part
            if part in PRODUCT_CODE_MAP:
                return PRODUCT_CODE_MAP[part]
            r = PRODUCT_TITLE_ALIASES.get(part)
            if r:
                return r
    # 纯产品码映射（p_cvm -> "CVM 云服务器"）
    if title in PRODUCT_CODE_MAP:
        return PRODUCT_CODE_MAP[title]
    return PRODUCT_TITLE_ALIASES.get(title, title)


def get_monitor_data(monitor, canon_title, rid):
    """从 monitor_summary.by_product[short_key][rid] 取监控数据。"""
    short_key = MONITOR_PROD_KEY.get(canon_title, "")
    by_product = monitor.get("by_product", {})
    # 先用规范短 key
    prod_mon = by_product.get(short_key, {})
    if not prod_mon:
        # 降级：遍历 by_product 找包含 short_key 的
        for k, v in by_product.items():
            if short_key and short_key in k:
                prod_mon = v
                break
    inst_data = prod_mon.get(rid, {})
    # monitor_summary 每条实例有 summary 和 raw 两层，judge 函数期望 summary 层
    return inst_data.get("summary", inst_data)


def get_inst_spec(inst, product):
    """从实例数据提取规格字符串。"""
    p = product.lower()
    if p in ("cvm", "cvm 云服务器"):
        cpu = inst.get("CPU", "?")
        mem = inst.get("Memory", 0)
        mem_gb = _cvm_mem_gb(mem)
        return f"{cpu}核{mem_gb}GB"
    if p in ("mysql", "云数据库mysql", "cdb"):
        cpu = inst.get("Cpu", "?")
        mem = inst.get("Memory", 0)
        mem_gb = _mysql_mem_gb(mem)
        if mem == 0:
            return "-（规格字段异常）"
        return f"{cpu}核{mem_gb}GB"
    if p in ("redis", "云数据库redis"):
        size = inst.get("Size", 0)
        size_gb = size / 1024 if size >= 1024 else size
        return f"{size_gb}GB"
    if p in ("cbs", "cbs 云硬盘"):
        size = inst.get("DiskSize", "?")
        dtype = inst.get("DiskType", "")
        return f"{size}GB {dtype}"
    if p in ("es elasticsearch", "es", "elasticsearch"):
        return inst.get("EsDomain", inst.get("InstanceId", ""))
    if p in ("clb", "clb 负载均衡", "负载均衡"):
        lb_type = inst.get("LoadBalancerType", "")
        lb_spec = inst.get("LoadBalancerTypeToSpec", "")
        net_type = inst.get("LoadBalancerTypeDesc", "")
        type_map = {"1": "内网", "2": "公网", "3": "内网IPv6", "4": "公网IPv6"}
        net_label = type_map.get(str(lb_type), f"类型{lb_type}" if lb_type else "")
        if lb_spec:
            return f"{net_label} {lb_spec}" if net_label else lb_spec
        if net_type:
            return net_type
        if net_label:
            return net_label
        return inst.get("LoadBalancerName", "-")
    if p in ("eip", "eip 弹性公网ip", "弹性公网ip"):
        addr = inst.get("Address", "")
        return addr or "-"
    if p in ("vpn", "vpn 网关"):
        bw = inst.get("InternetMaxBandwidthOut", inst.get("Bandwidth", ""))
        return f"带宽{bw}Mbps" if bw else "-"
    if p in ("emr", "emr 弹性mapreduce"):
        return inst.get("ClusterName", inst.get("Id", "-"))
    if p in ("dc", "dc 专线接入"):
        bw = inst.get("Bandwidth", "")
        return f"带宽{bw}Mbps" if bw else "-"
    if p in ("cynosdb", "cynosdb tdsql-c", "云数据库cynosdb"):
        cpu = inst.get("Cpu", "?")
        mem = inst.get("Memory", 0) or 0
        return f"{cpu}核{mem}GB"
    if p in ("mariadb", "云数据库mariadb"):
        mem = inst.get("Memory", 0) or 0
        storage = inst.get("Storage", 0) or 0
        return f"{mem}MB/{storage}GB" if mem else "-"
    # 通用
    for key in ("InstanceType", "DBInstanceType", "TypeName", "Spec"):
        if inst.get(key):
            return str(inst[key])
    return "-"


# ─── advisor 结果构建 ─────────────────────────────────────────────

def build_advisor_finding(hit, lookup, disc, bill_summary, head_map,
                          disc_count_override=None, advisor_hits=None):
    """构建 advisor 独家命中的 finding。"""
    rid = hit.get("instance_id", "")
    product_raw = hit.get("product_key", hit.get("product", ""))
    # 规范化：advisor product_key 是短名（"cvm"/"clb"...），需映射为规范标题
    product = resolve_product_title(product_raw)
    suggestion = hit.get("strategy_name", hit.get("suggestion", hit.get("title", "")))
    issue_type = hit.get("strategy_name", suggestion)
    level = hit.get("level", 1)
    risk_days = hit.get("risk_days", 0)
    severity = map_advisor_level(hit)
    risk_label = SEVERITY_RISK_LABEL.get(severity, "[低]")
    strategy_id = str(hit.get("strategy_id", ""))

    # 规格补全（F36）
    spec = _find_spec_in_disc(disc, rid, product)

    # 月省计算
    ratio_key = _advisor_strategy_to_ratio(strategy_id, product)
    # disc_count：优先用外部传入，否则查 disc，最后用同产品 advisor 命中数兜底（LH 等无 disc 条目产品）
    if disc_count_override is not None and disc_count_override > 0:
        disc_count = disc_count_override
    else:
        disc_count = _count_product_in_disc(disc, product)
    if disc_count == 0 and advisor_hits:
        # 用同产品 advisor 命中数作为均摊分母兜底
        disc_count = sum(1 for h in advisor_hits
                         if resolve_product_title(h.get("product_key", "")) == product)
    saving, saving_display = calc_saving(rid, product, ratio_key, lookup, bill_summary, disc_count)

    # advisor target_spec + suggestion 增强
    sn_lower = (suggestion or "").lower()
    if "计费模式" in suggestion or "包年包月" in suggestion or "按量" in suggestion:
        target_spec = "转包年包月"
    elif "利用率" in suggestion or "降配" in suggestion or "缩容" in suggestion:
        # 从 disc 中查找实例规格，生成具体降配建议
        inst_info = _find_inst_in_disc(disc, rid, product)
        if inst_info:
            target_spec, suggestion = _advisor_downsize_spec(inst_info, product, suggestion)
        else:
            target_spec = "降配"
    elif "闲置" in suggestion or "释放" in suggestion:
        target_spec = "释放实例"
    elif "定时关机" in suggestion or "关机" in suggestion:
        target_spec = "配置定时关机"
    else:
        target_spec = "-"

    return {
        "product": product,
        "rid": rid,
        "name": hit.get("instance_name", hit.get("name", "")),
        "spec": spec,
        "owner": head_map.get(rid, "-"),
        "issue_type": issue_type,
        "suggestion": suggestion,
        "target_spec": target_spec,
        "severity": severity,
        "risk": risk_label,
        "saving": saving,
        "saving_display": saving_display,
        "source": "advisor",
        "coverage": None,
        "coverage_note": "",
        "advisor_level": level,
        "advisor_risk_days": risk_days,
    }


def _find_generic_rule_key(canon_title, product_rules):
    """从 product_rules.yaml 中查找匹配的产品规则 key。

    匹配逻辑:
    1. 精确匹配 display_name
    2. 产品中文名匹配
    3. 关键词模糊匹配
    """
    if not product_rules:
        return None
    # 跳过 defaults 和 evolution_log
    skip = {"defaults", "evolution_log"}
    for key, config in product_rules.items():
        if key in skip or not isinstance(config, dict):
            continue
        display_name = config.get("display_name", "")
        if display_name == canon_title or key == canon_title:
            return key
    # 模糊匹配
    title_lower = canon_title.lower()
    for key, config in product_rules.items():
        if key in skip or not isinstance(config, dict):
            continue
        display_name = config.get("display_name", "").lower()
        if key.lower() in title_lower or title_lower in key.lower():
            return key
        if display_name and (display_name in title_lower or title_lower in display_name):
            return key
    return None


def evolve_rules_from_llm(product_name, new_rule, confirmed_by="llm"):
    """将 LLM 分析确认的规则回写到 product_rules.yaml。

    这是自进化机制的核心：LLM 分析 unhandled_resources 后，
    将发现的模式固化到 YAML 规则中，下次运行不需要再调 LLM。

    Args:
        product_name: 产品名（如 "CCN 云联网"）
        new_rule: 规则 dict，格式同 product_rules.yaml 中的 rules 条目
        confirmed_by: 确认来源（"llm" / "user"）

    Returns:
        bool: 是否成功写入
    """
    if not _HAS_YAML:
        return False

    rules_path = os.path.join(os.path.dirname(__file__), "product_rules.yaml")
    if not os.path.exists(rules_path):
        return False

    with open(rules_path, "r", encoding="utf-8") as f:
        rules_data = yaml.safe_load(f) or {}

    # 找到或创建产品规则条目
    product_key = None
    skip = {"defaults", "evolution_log"}
    for key, config in rules_data.items():
        if key in skip or not isinstance(config, dict):
            continue
        if config.get("display_name") == product_name:
            product_key = key
            break

    if product_key is None:
        # 创建新产品条目
        product_key = product_name.lower().replace(" ", "_").replace("（", "").replace("）", "")
        rules_data[product_key] = {
            "display_name": product_name,
            "rid_field": "InstanceId",
            "name_field": "InstanceName",
            "spec_fields": [],
            "spec_fn": "generic_spec",
            "rules": [new_rule],
        }
    else:
        # 追加规则（避免重复）
        existing_rules = rules_data[product_key].get("rules", [])
        existing_names = {r.get("name") for r in existing_rules}
        if new_rule.get("name") not in existing_names:
            existing_rules.append(new_rule)
            rules_data[product_key]["rules"] = existing_rules

    # 记录进化日志
    if "evolution_log" not in rules_data:
        rules_data["evolution_log"] = []
    rules_data["evolution_log"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "product": product_name,
        "finding": f"{new_rule.get('name', '')}: {new_rule.get('condition', '')}",
        "confirmed_by": confirmed_by,
        "promoted_to_rule": True,
    })

    # 写回 YAML
    with open(rules_path, "w", encoding="utf-8") as f:
        yaml.dump(rules_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return True


def _find_spec_in_disc(disc, rid, product):
    """从 resource_discovery.json 中按 rid 查找规格。"""
    for prod_title, regions in (disc.get("results") or {}).items():
        if not isinstance(regions, dict):
            continue
        for region, instances in regions.items():
            if not isinstance(instances, list):
                continue
            for inst in instances:
                inst_rid = (inst.get("InstanceId") or inst.get("InstanceID") or
                            inst.get("DiskId") or inst.get("DBInstanceId") or "")
                if inst_rid == rid:
                    return get_inst_spec(inst, prod_title)
    return "-"


def _find_inst_in_disc(disc, rid, product):
    """从 resource_discovery.json 中按 rid 查找完整实例信息（dict），用于降配建议生成。"""
    for prod_title, regions in (disc.get("results") or {}).items():
        if not isinstance(regions, dict):
            continue
        for region, instances in regions.items():
            if not isinstance(instances, list):
                continue
            for inst in instances:
                inst_rid = (inst.get("InstanceId") or inst.get("InstanceID") or
                            inst.get("DiskId") or inst.get("DBInstanceId") or "")
                if inst_rid == rid:
                    inst["_prod_title"] = prod_title
                    inst["_region"] = region
                    return inst
    return None


def _advisor_downsize_spec(inst, product, original_suggestion):
    """根据实例信息和产品类型，生成具体降配建议。
    返回 (target_spec, suggestion)，例如 ("降至 2核4GB", "4核8GB → 2核4GB")。
    """
    prod_title = inst.get("_prod_title", product)

    # MySQL / CynosDB / MariaDB
    if any(k in prod_title for k in ("MySQL", "CynosDB", "MariaDB")):
        cpu_cores = inst.get("Cpu", 0) or 0
        mem_mb = inst.get("Memory", 0) or 0
        mem_gb = _mysql_mem_gb(mem_mb)
        spec_str = f"{cpu_cores}核{mem_gb}GB"
        target_spec = _mysql_target_spec(cpu_cores, mem_gb, "低利用率可降配")
        if target_spec and target_spec not in ("-", "建议降配"):
            if target_spec == "已达最小规格":
                suggestion = f"{spec_str}（已达最小规格，建议评估是否释放）"
            else:
                target_short = target_spec.replace("降至 ", "")
                suggestion = f"{spec_str} → {target_short}"
        else:
            suggestion = original_suggestion
        return target_spec, suggestion

    # Redis
    if "Redis" in prod_title:
        size_mb = inst.get("Size", 0) or inst.get("Memory", 0) or 0
        if size_mb > 0:
            size_gb = size_mb / 1024 if size_mb >= 1024 else size_mb
            # 建议降至一半
            target_mb = size_mb / 2
            tiers = [256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
            target_mb = min((t for t in tiers if t >= target_mb), default=size_mb)
            if target_mb < size_mb:
                target_gb = target_mb / 1024 if target_mb >= 1024 else target_mb
                target_spec = f"降至{target_gb}GB"
                suggestion = f"{size_gb}GB → {target_gb}GB"
                return target_spec, suggestion
        return "降配", original_suggestion

    # MongoDB
    if "MongoDB" in prod_title:
        cpu_cores, mem_gb, spec_str = _mongodb_spec_str(inst)
        target_spec = _mongodb_target_spec(cpu_cores, mem_gb)
        if target_spec == "已达最小规格":
            suggestion = f"{spec_str}（已达最小规格，建议评估是否释放）"
        elif target_spec != "降配":
            target_short = target_spec.replace("降至 ", "")
            suggestion = f"{spec_str} → {target_short}"
        else:
            suggestion = original_suggestion
        return target_spec, suggestion

    # CVM
    if "CVM" in prod_title or "云服务器" in prod_title:
        cpu_cores = inst.get("CPU", 0) or 0
        mem_gb = _cvm_mem_gb(inst.get("Memory", 0))
        spec_str = f"{cpu_cores}核{mem_gb}GB"
        target_spec = _cvm_target_spec(cpu_cores, mem_gb, "低利用率")
        if target_spec and target_spec != "-":
            target_short = target_spec.replace("降至 ", "")
            suggestion = f"{spec_str} → {target_short}"
        else:
            suggestion = original_suggestion
        return target_spec, suggestion

    # ES / PostgreSQL 等其他产品
    return "降配", original_suggestion


def _count_product_in_disc(disc, product):
    """统计 resource_discovery 中某产品的资源总数（用于均摊）。
    product 可以是规范标题（"MySQL"）或产品码（"p_cdb"）。
    """
    # 先通过 PRODUCT_JUDGE_MAP 找产品码，再直接查 disc
    canon = resolve_product_title(product)
    entry = PRODUCT_JUDGE_MAP.get(canon)
    if entry:
        product_code = entry[4]  # (judge_fn, rid_field, name_field, spec_fn, product_code)
        regions = (disc.get("results") or {}).get(product_code, {})
        cnt = sum(len(v) for v in regions.values() if isinstance(v, list))
        if cnt > 0:
            return cnt

    # 兜底：按 product_code 直接查（如传入 "p_cdb"）
    if product in (disc.get("results") or {}):
        regions = disc["results"][product]
        return sum(len(v) for v in regions.values() if isinstance(v, list))

    # 最后兜底：模糊匹配节标题
    p = product.lower()
    for prod_title, regions in (disc.get("results") or {}).items():
        if p in prod_title.lower():
            cnt = sum(len(v) for v in (regions or {}).values() if isinstance(v, list))
            if cnt > 0:
                return cnt
    return 0


def _advisor_strategy_to_ratio(strategy_id, product):
    """advisor strategy_id → ratio_key 映射（简化版）。"""
    p = product.lower()
    # LH (strategy 7302)
    if strategy_id == "7302":
        return "lh_advisor"
    # 按产品给默认路径
    ratio_defaults = {
        "cvm": "cvm_downsize_half",
        "cbs": "cbs_release",
        "cdb": "mysql_downsize",
        "redis": "redis_downsize",
        "mongodb": "mongo_downsize",
        "postgres": "pgsql_downsize",
        "clb": "clb_release",
        "eip": "eip_release",
        "nat": "nat_downsize",
        "es": "es_downsize",
        "ckafka": "ckafka_downsize",
        "tke": "tke_downsize",
        "cos": "cos_infrequent",
        "lh": "lh_advisor",
        "svh": "lh_advisor",
        "lmp": "lh_advisor",
        "轻量": "lh_advisor",
    }
    for key, ratio_key in ratio_defaults.items():
        if key in p:
            return ratio_key
    return "cvm_release"  # 兜底 100%


# ─── 主判定循环 ───────────────────────────────────────────────────

def _issue_type_match(adv_issue, copt_issue):
    """判断 advisor issue_type 和 costopt issue_type 是否语义匹配，
    匹配时才允许用 costopt 的 severity 覆盖 advisor。"""
    adv = (adv_issue or "").lower()
    copt = (copt_issue or "").lower()
    # 利用率类
    if any(k in adv for k in ("利用率", "利用")) and any(k in copt for k in ("利用率", "利用", "闲置", "低利用率")):
        return True
    # 闲置类（advisor "闲置" 匹配 costopt "无监听器"/"无后端绑定"/"疑似闲置"等，语义同属"资源无用"）
    if any(k in adv for k in ("闲置",)) and any(k in copt for k in ("闲置", "疑似闲置", "无监听器", "无后端绑定", "未挂载", "未绑定", "空集群", "无客户端连接", "近无IO")):
        return True
    # 反向：costopt "无监听器"/"无后端绑定" 匹配 advisor "闲置"
    if any(k in adv for k in ("无监听器", "无后端绑定", "未挂载", "未绑定", "空集群", "无客户端连接", "近无IO")) and any(k in copt for k in ("闲置",)):
        return True
    # 计费模式类
    if any(k in adv for k in ("计费", "按量", "包年", "包月")) and any(k in copt for k in ("计费", "按量", "包年", "包月", "转包")):
        return True
    # 停机/关机类
    if any(k in adv for k in ("停机", "关机")) and any(k in copt for k in ("停机", "关机", "定时")):
        return True
    # 降配/缩容类
    if any(k in adv for k in ("降配", "缩容", "规格", "降带宽")) and any(k in copt for k in ("降配", "缩容", "规格", "降带宽")):
        return True
    # 释放类
    if any(k in adv for k in ("释放", "删除", "退订")) and any(k in copt for k in ("释放", "删除", "退订")):
        return True
    # 完全匹配
    if adv == copt:
        return True
    return False


def run_judgment(disc, monitor, lookup, bill_summary, advisor, head_map):
    """遍历所有产品和资源，执行判定，返回 (findings, protected, exempted, other_products)。"""
    findings = []
    findings_by_rid = {}
    protected = []
    exempted = []
    other_products = []

    advisor_hits = advisor.get("hits", [])
    advisor_hit_map = {h["instance_id"]: h for h in advisor_hits}
    scan_status = advisor.get("scan_status", "denied")

    for prod_title, regions in (disc.get("results") or {}).items():
        canon_title = resolve_product_title(prod_title)
        judge_entry = PRODUCT_JUDGE_MAP.get(canon_title)

        if not isinstance(regions, dict):
            continue

        # 统计该产品总资源数（用于均摊），同时记录每条实例的地域 key
        all_insts = []
        for region, instances in regions.items():
            if isinstance(instances, list):
                for inst in instances:
                    inst["_region_key"] = region  # 注入地域信息供后续提取
                all_insts.extend(instances)
        disc_count = len(all_insts)

        if judge_entry is None:
            # 无专用 judge 函数 → 尝试通用指标驱动判定（Layer 2）
            product_rules = _load_product_rules()
            generic_key = _find_generic_rule_key(canon_title, product_rules)

            if generic_key:
                rules_config = product_rules.get(generic_key, {})
                rid_field_g = rules_config.get("rid_field", "InstanceId")
                name_field_g = rules_config.get("name_field", "InstanceName")
                product_code_g = f"p_{generic_key}"

                for inst in all_insts:
                    rid = (inst.get(rid_field_g) or inst.get("InstanceId")
                           or inst.get("ResourceId") or "")
                    name = inst.get(name_field_g) or inst.get("InstanceName") or ""
                    if not rid:
                        continue

                    # 保护 / 豁免检查
                    is_prot, prot_reason = is_protected_f7(inst, canon_title)
                    if is_prot:
                        protected.append({"product": canon_title, "rid": rid, "name": name,
                                          "reason": prot_reason, "spec": get_inst_spec(inst, canon_title),
                                          "region": _extract_region(inst, canon_title) or "-"})
                        continue
                    exemption = check_exemption(inst, product_code_g)
                    if exemption == "full_skip":
                        exempted.append({"product": canon_title, "rid": rid, "name": name,
                                         "exemption_type": "完全豁免",
                                         "expire": get_tag(inst.get("Tags") or [], "finops-skip-expire") or "永久"})
                        continue

                    mon_data = get_monitor_data(monitor, canon_title, rid)
                    gen_findings = generic_metric_judge(
                        inst, mon_data, lookup, bill_summary, head_map, disc_count,
                        canon_title, product_code_g, rules_config)

                    for f in gen_findings:
                        if "issue" not in f:
                            f["issue"] = f.get("issue_type") or f.get("suggestion") or "-"
                        if "optimize_path" not in f or not f["optimize_path"]:
                            f["optimize_path"] = _issue_to_optimize_path(f.get("issue_type") or f.get("issue") or "")
                        cf_full = {
                            "product": canon_title,
                            "rid": rid,
                            "name": name,
                            "spec": get_inst_spec(inst, canon_title),
                            "owner": head_map.get(rid, "-"),
                            "region": _extract_region(inst, canon_title) or "-",
                            **f,
                            "source": "generic",
                        }
                        findings.append(cf_full)

                # 如果通用规则也没有匹配任何 finding，记录 unhandled
                if not generic_key or not rules_config.get("rules"):
                    total_cost = sum(
                        lookup.get(
                            (inst.get("InstanceId") or inst.get("DiskId")
                             or inst.get("ResourceId") or ""), {}
                        ).get("cost", 0)
                        for inst in all_insts
                    )
                    other_products.append({
                        "product_title": prod_title,
                        "resource_count": disc_count,
                        "total_bill_cost": total_cost,
                    })
            else:
                # 完全没有规则 → 输出 unhandled_resources.json 供 LLM 分析
                total_cost = sum(
                    lookup.get(
                        (inst.get("InstanceId") or inst.get("DiskId")
                         or inst.get("ResourceId") or ""), {}
                    ).get("cost", 0)
                    for inst in all_insts
                )
                other_products.append({
                    "product_title": prod_title,
                    "resource_count": disc_count,
                    "total_bill_cost": total_cost,
                })
            continue

        judge_fn, rid_field, name_field, spec_fn, product_code = judge_entry

        for inst in all_insts:
            rid = inst.get(rid_field) or inst.get("InstanceId") or inst.get("ResourceId") or ""
            name = inst.get(name_field) or inst.get("InstanceName") or ""
            if not rid:
                continue

            # F7 保护检查
            is_prot, prot_reason = is_protected_f7(inst, canon_title)
            if is_prot:
                protected.append({
                    "product": canon_title,
                    "rid": rid,
                    "name": name,
                    "reason": prot_reason,
                    "spec": get_inst_spec(inst, canon_title),
                    "region": _extract_region(inst, canon_title) or "-",
                })
                continue

            # F9 豁免检查
            exemption = check_exemption(inst, product_code)
            if exemption == "full_skip":
                exempted.append({
                    "product": canon_title,
                    "rid": rid,
                    "name": name,
                    "exemption_type": "完全豁免",
                    "expire": get_tag(inst.get("Tags") or [], "finops-skip-expire") or "永久",
                })
                continue

            # advisor 命中 → 也执行 costopt 判定，两者都保留（§7.3 修正）
            if rid in advisor_hit_map:
                hit = advisor_hit_map[rid]
                f = build_advisor_finding(hit, lookup, disc, bill_summary, head_map,
                                          disc_count_override=disc_count,
                                          advisor_hits=advisor_hits)
                # 执行 costopt 判定
                mon_data = get_monitor_data(monitor, canon_title, rid)
                costopt_findings = judge_fn(inst, mon_data, lookup, bill_summary, head_map, disc_count)

                if costopt_findings:
                    # 仅取语义匹配的 costopt findings 来覆盖 advisor severity，
                    # 避免 advisor "利用率不足" 被 costopt "按量长期运行" 的 critical 误覆盖
                    adv_issue = f.get("issue_type", "")
                    matched = [cf for cf in costopt_findings
                               if _issue_type_match(adv_issue, cf.get("issue_type", ""))]
                    if matched:
                        best = max(matched,
                                   key=lambda x: SEVERITY_ORDER.get(x["severity"], 0))
                        adv_sv = SEVERITY_ORDER.get(f["severity"], 0)
                        copt_sv = SEVERITY_ORDER.get(best["severity"], 0)
                        if copt_sv > adv_sv:
                            f["severity"] = best["severity"]
                            f["risk"] = best["risk"]
                    f["source"] = "advisor+costopt"
                else:
                    f["source"] = "advisor"

                f["spec"] = get_inst_spec(inst, canon_title) or f.get("spec", "-")
                f["owner"] = head_map.get(rid, "-")

                # 应用 relax
                if exemption == "relax":
                    f["risk"] = apply_relax(f["risk"])

                findings.append(f)
                findings_by_rid[rid] = f

                # 额外：将 costopt 独有的 finding 也作为独立条目保留
                # 规则：只有与 advisor issue_type 语义不匹配的才保留为独立条目，
                # 语义匹配的（如 advisor "闲置" + costopt "无监听器"）用 costopt 的更精准结论覆盖
                adv_issue = f.get("issue_type", "")
                for cf in (costopt_findings or []):
                    cf_issue = cf.get("issue_type", "")
                    if not cf_issue:
                        continue
                    if _issue_type_match(adv_issue, cf_issue):
                        # 语义匹配：用 costopt 更精准的结论替换 advisor 的模糊结论
                        copt_sv = SEVERITY_ORDER.get(cf.get("severity", "low"), 0)
                        adv_sv = SEVERITY_ORDER.get(f.get("severity", "low"), 0)
                        if copt_sv >= adv_sv:
                            # costopt 更精准或同等级，用 costopt 的 issue_type/suggestion/target_spec 覆盖
                            f["issue_type"] = cf_issue
                            f["suggestion"] = cf.get("suggestion", f.get("suggestion", ""))
                            f["target_spec"] = cf.get("target_spec", f.get("target_spec", "-"))
                            if copt_sv > adv_sv:
                                f["severity"] = cf["severity"]
                                f["risk"] = cf.get("risk", f.get("risk", ""))
                            f["saving"] = cf.get("saving", f.get("saving"))
                            f["saving_display"] = cf.get("saving_display", f.get("saving_display", ""))
                    else:
                        # 语义不匹配（如 advisor "利用率不足" + costopt "按量长期运行"），保留为独立条目
                        cf_full = {
                            "product": canon_title,
                            "rid": rid,
                            "name": name,
                            "spec": get_inst_spec(inst, canon_title) or cf.get("spec", "-"),
                            "owner": head_map.get(rid, "-"),
                            "issue_type": cf_issue,
                            "suggestion": cf.get("suggestion", ""),
                            "target_spec": cf.get("target_spec", "-"),
                            "severity": cf.get("severity", "low"),
                            "risk": cf.get("risk", SEVERITY_RISK_LABEL.get(cf.get("severity", "low"), "[低]")),
                            "saving": cf.get("saving"),
                            "saving_display": cf.get("saving_display", ""),
                            "source": "costopt",
                            "coverage": cf.get("coverage"),
                            "coverage_note": cf.get("coverage_note", ""),
                        }
                        if exemption == "relax":
                            cf_full["risk"] = apply_relax(cf_full["risk"])
                        findings.append(cf_full)

                continue

            # 普通 costopt 判定
            mon_data = get_monitor_data(monitor, canon_title, rid)
            inst_findings = judge_fn(inst, mon_data, lookup, bill_summary, head_map, disc_count)

            for f in inst_findings:
                if exemption == "no_downgrade" and "降配" in f.get("suggestion", ""):
                    continue  # F9 no_downgrade：跳过降配建议
                if exemption == "relax":
                    f["risk"] = apply_relax(f["risk"])

                spec = get_inst_spec(inst, canon_title)
                finding = {
                    "product": canon_title,
                    "rid": rid,
                    "name": name,
                    "spec": spec,
                    "owner": head_map.get(rid, "-"),
                    **f,
                    "source": "costopt",
                }
                findings.append(finding)
                findings_by_rid[rid] = finding

    # advisor 独家路径（未在 resource_discovery 中扫描到的）
    for rid, hit in advisor_hit_map.items():
        if rid not in findings_by_rid:
            f = build_advisor_finding(hit, lookup, disc, bill_summary, head_map,
                                      advisor_hits=advisor_hits)
            findings.append(f)
            findings_by_rid[rid] = f

    # ── 统一补齐 region / baseline / 兼容字段 ──
    # 构建 rid → (inst, canon_title, product_code, disc_count) 的快速索引
    rid_meta = {}
    for prod_title, regions_data in (disc.get("results") or {}).items():
        canon_title = resolve_product_title(prod_title)
        judge_entry = PRODUCT_JUDGE_MAP.get(canon_title)
        if not judge_entry:
            continue
        _, rid_field, _, _, product_code = judge_entry
        all_insts_p = []
        for region_key, instances in (regions_data or {}).items():
            if isinstance(instances, list):
                all_insts_p.extend(instances)
        disc_count_p = len(all_insts_p)
        for inst in all_insts_p:
            inst_rid = inst.get(rid_field) or inst.get("InstanceId") or inst.get("ResourceId") or ""
            if inst_rid:
                rid_meta[inst_rid] = (inst, canon_title, product_code, disc_count_p, region_key)

    for f in findings:
        rid = f.get("rid") or f.get("resource_id") or ""
        # region
        if not f.get("region") or f.get("region") == "-":
            meta = rid_meta.get(rid)
            if meta:
                inst, canon_title, _, _, region_key = meta
                f["region"] = _extract_region(inst, canon_title) or region_key or "-"
            elif f.get("source") == "advisor":
                # advisor 独家：尝试从 hit 找
                f["region"] = "-"
        # baseline
        if "baseline" not in f or not f["baseline"]:
            meta = rid_meta.get(rid)
            if meta:
                _, canon_title, product_code, disc_count_p, _ = meta
                f["baseline"] = _extract_baseline(rid, lookup, bill_summary, product_code, disc_count_p)
            else:
                f["baseline"] = 0
        # 兼容 render 期望的字段名
        if "resource_id" not in f:
            f["resource_id"] = f.get("rid") or "-"
        if "resource_name" not in f:
            f["resource_name"] = f.get("name") or ""
        if "issue" not in f:
            f["issue"] = f.get("issue_type") or f.get("suggestion") or "-"
        # optimize_path（优化路径标准化分类）
        if "optimize_path" not in f or not f["optimize_path"]:
            f["optimize_path"] = _issue_to_optimize_path(f.get("issue_type") or f.get("issue") or "")

    return findings, protected, exempted, other_products, scan_status


# ─── 智能洞察生成 ─────────────────────────────────────────────────

def _build_insights(findings, protected, exempted, other_products, bill_summary, scan_status):
    """基于巡检结果生成智能洞察列表（增强版：多维度分类 + 执行优先级 + 产品细节）。"""
    insights = []

    # advisor 不可用警告
    if scan_status == "denied":
        insights.append({
            "level": "danger",
            "text": "⚠️ 云端策略巡检不可用，以下结论仅基于本地账单+监控自判，建议开通获取更全面的优化建议",
        })

    if not findings:
        insights.append({"level": "info", "text": "✅ 未发现明显闲置资源，当前资源利用率健康"})
        return insights

    # ── 按风险等级统计 ──
    by_sev = {}
    by_sev_saving = {}
    for f in findings:
        sev = f.get("severity", "low")
        by_sev[sev] = by_sev.get(sev, 0) + 1
        by_sev_saving[sev] = by_sev_saving.get(sev, 0) + (f.get("saving") or 0)

    critical_n = by_sev.get("critical", 0)
    high_n = by_sev.get("high", 0)
    medium_high_n = by_sev.get("medium_high", 0)
    medium_n = by_sev.get("medium", 0)
    low_n = by_sev.get("low", 0)

    # ── 严重/高优洞察 ──
    if critical_n > 0:
        critical_saving = by_sev_saving.get("critical", 0)
        insights.append({
            "level": "danger",
            "text": f"🔴 发现 {critical_n} 个严重浪费资源（停机按量/无后端CLB/按量长期运行等），预计月省 ¥{int(critical_saving):,}，建议立即处理",
        })
    if high_n > 0:
        high_saving = by_sev_saving.get("high", 0)
        insights.append({
            "level": "warn",
            "text": f"🟠 发现 {high_n} 个高优化项（闲置数据库/未挂载磁盘等），预计月省 ¥{int(high_saving):,}，建议优先评审",
        })
    if medium_high_n > 0:
        mh_saving = by_sev_saving.get("medium_high", 0)
        insights.append({
            "level": "warn",
            "text": f"🟠 发现 {medium_high_n} 个计费模式不合理资源，预计月省 ¥{int(mh_saving):,}",
        })

    # ── 按产品统计 ──
    by_prod = {}
    by_prod_saving = {}
    by_prod_issue = {}
    for f in findings:
        p = f.get("product", "其他")
        by_prod[p] = by_prod.get(p, 0) + 1
        by_prod_saving[p] = by_prod_saving.get(p, 0) + (f.get("saving") or 0)
        issue = f.get("issue_type") or ""
        by_prod_issue.setdefault(p, {}).setdefault(issue, 0)
        by_prod_issue[p][issue] += 1

    # ── 按优化路径分类 ──
    by_issue = {}
    by_issue_saving = {}
    for f in findings:
        issue = f.get("issue_type") or "其他"
        by_issue[issue] = by_issue.get(issue, 0) + 1
        by_issue_saving[issue] = by_issue_saving.get(issue, 0) + (f.get("saving") or 0)

    # 最大节省产品
    if by_prod_saving:
        top_prod = max(by_prod_saving, key=by_prod_saving.get)
        top_saving = by_prod_saving[top_prod]
        if top_saving > 0:
            issues_detail = "、".join(f"{k}{v}个" for k, v in (by_prod_issue.get(top_prod, {})).items())
            insights.append({
                "level": "info",
                "text": f"📊 {top_prod} 优化空间最大（{issues_detail}），预计月省 ¥{int(top_saving):,}",
            })

    # ── 各产品专属洞察 ──

    # CVM 细分
    cvm_findings = [f for f in findings if f.get("product") == "CVM 云服务器"]
    if cvm_findings:
        cvm_saving = sum(f.get("saving") or 0 for f in cvm_findings)
        cvm_issues = {}
        for f in cvm_findings:
            it = f.get("issue_type") or "其他"
            cvm_issues[it] = cvm_issues.get(it, 0) + 1
        detail = "、".join(f"{k}{v}台" for k, v in cvm_issues.items())
        insights.append({
            "level": "info",
            "text": f"💻 {len(cvm_findings)} 台 CVM 有优化空间（{detail}），预计月省 ¥{int(cvm_saving):,}",
        })

    # CLB 细分
    clb_no_backend = sum(1 for f in findings
                         if f.get("product") == "CLB 负载均衡" and "无后端" in (f.get("issue_type") or ""))
    clb_no_conn = sum(1 for f in findings
                      if f.get("product") == "CLB 负载均衡" and "无连接" in (f.get("issue_type") or ""))
    clb_saving = sum(f.get("saving") or 0 for f in findings if f.get("product") == "CLB 负载均衡")
    if clb_no_backend > 0 or clb_no_conn > 0:
        detail_parts = []
        if clb_no_backend > 0:
            detail_parts.append(f"{clb_no_backend}个无后端")
        if clb_no_conn > 0:
            detail_parts.append(f"{clb_no_conn}个无连接")
        insights.append({
            "level": "warn",
            "text": f"🔗 CLB 优化空间：{'、'.join(detail_parts)}，预计月省 ¥{int(clb_saving):,}",
        })

    # CBS 细分
    cbs_unattached = sum(1 for f in findings
                         if f.get("product") == "CBS 云硬盘" and "未挂载" in (f.get("issue_type") or ""))
    cbs_ssd_low = sum(1 for f in findings
                      if f.get("product") == "CBS 云硬盘" and "降级" in (f.get("issue_type") or ""))
    if cbs_unattached > 0 or cbs_ssd_low > 0:
        cbs_saving = sum(f.get("saving") or 0 for f in findings if f.get("product") == "CBS 云硬盘")
        detail_parts = []
        if cbs_unattached > 0:
            detail_parts.append(f"{cbs_unattached}个未挂载")
        if cbs_ssd_low > 0:
            detail_parts.append(f"{cbs_ssd_low}个SSD可降级")
        insights.append({
            "level": "info",
            "text": f"💾 CBS 优化空间：{'、'.join(detail_parts)}，预计月省 ¥{int(cbs_saving):,}",
        })

    # 数据库汇总
    db_products = {"MySQL", "云数据库MySQL", "Redis", "云数据库Redis", "MongoDB", "云数据库MongoDB",
                   "PostgreSQL", "云数据库PostgreSQL", "CynosDB TDSQL-C", "MariaDB"}
    db_findings = [f for f in findings if f.get("product") in db_products]
    if db_findings:
        db_saving = sum(f.get("saving") or 0 for f in db_findings)
        db_idle = sum(1 for f in db_findings if "闲置" in (f.get("issue_type") or ""))
        db_downsize = sum(1 for f in db_findings if "降配" in (f.get("issue_type") or ""))
        db_annual = sum(1 for f in db_findings if "按量" in (f.get("issue_type") or ""))
        detail_parts = []
        if db_idle > 0:
            detail_parts.append(f"{db_idle}个闲置")
        if db_downsize > 0:
            detail_parts.append(f"{db_downsize}个可降配")
        if db_annual > 0:
            detail_parts.append(f"{db_annual}个可转包年")
        insights.append({
            "level": "warn",
            "text": f"🗄 数据库优化空间：{'、'.join(detail_parts)}，预计月省 ¥{int(db_saving):,}",
        })

    # ── 按优化路径 Top5 ──
    sorted_issues = sorted(by_issue_saving.items(), key=lambda x: -x[1])[:5]
    if sorted_issues:
        path_detail = "、".join(f"{k}({v}个·¥{int(by_issue_saving.get(k, 0)):,})" for k, v in sorted_issues)
        insights.append({
            "level": "info",
            "text": f"📈 优化路径排名：{path_detail}",
        })

    # ── 执行优先级建议 ──
    total_saving = sum(by_sev_saving.values())
    immediate_saving = by_sev_saving.get("critical", 0) + by_sev_saving.get("low", 0)
    if total_saving > 0:
        insights.append({
            "level": "info",
            "text": f"⚡ 执行建议：严重+低风险项预计月省 ¥{int(immediate_saving):,}（占比{int(immediate_saving / total_saving * 100) if total_saving else 0}%），可立即执行",
        })

    # ── 保护资源 ──
    if protected:
        insights.append({
            "level": "info",
            "text": f"🛡 {len(protected)} 个关键资源已自动豁免闲置判定（主备/只读/副本），不做优化建议",
        })

    # ── 豁免资源 ──
    if exempted:
        insights.append({
            "level": "info",
            "text": f"🏷 {len(exempted)} 个资源因 finops-skip 标签豁免，不纳入优化判定",
        })

    # ── 其他产品 ──
    if other_products:
        other_names = [op.get("product_title", "") for op in other_products[:3]]
        insights.append({
            "level": "info",
            "text": f"📋 {len(other_products)} 类产品暂无资源级扫描 API（{', '.join(other_names)}），仅展示账单费用",
        })

    return insights


# ─── 主流程 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="costopt Step 3 判定脚本")
    parser.add_argument("--work-dir", type=str, required=True, help="工作目录")
    parser.add_argument("--account-name", type=str, default="", help="账户名（用于报告）")
    parser.add_argument("--cloud", type=str, default="tencent",
                        help="云厂商（落盘命名用，与 prepare 的 --cloud 一致）")
    parser.add_argument("--project", type=str, default="",
                        help="项目/账号代号（如 ADC/TSP/YX）；用于稳定命名 costopt-{cloud}-{project}-{uin}.html，"
                             "避免多账号并发用波动昵称导致重名/覆盖")
    parser.add_argument("--render-html", action="store_true", default=True,
                        help="渲染 HTML 看板（默认开启）")
    parser.add_argument("--no-render-html", action="store_false", dest="render_html",
                        help="跳过 HTML 渲染（仅调试场景）")
    parser.add_argument("--report-dir", type=str, default="",
                        help="HTML 最终落盘目录（直接使用，不再拼接 reports/{uin}/{date}）；"
                             "缺省时落到 $WORKBUDDY_WORKSPACE/reports/{uin}/{YYYY-MM-DD}")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh", help="report language")
    args = parser.parse_args()

    work_dir = args.work_dir

    # ── F-STEP3-PREREQ 校验 ──
    required_files = [
        "resource_discovery.json",
        "billing_lookup.json",
        "monitor_summary.json",
        "advisor_cost.json",
    ]
    missing = [f for f in required_files if not os.path.exists(os.path.join(work_dir, f))]
    if missing:
        print(json.dumps({
            "status": "prereq_fail",
            "error": f"PREREQ FAIL: 以下产物文件缺失，Step 3 不得开始：{missing}",
            "missing": missing,
        }, ensure_ascii=False))
        sys.exit(1)

    # ── 加载产物文件 ──
    disc = load_json(os.path.join(work_dir, "resource_discovery.json"))
    lookup = load_json(os.path.join(work_dir, "billing_lookup.json"))
    monitor = load_json(os.path.join(work_dir, "monitor_summary.json"))
    advisor = load_json(os.path.join(work_dir, "advisor_cost.json"))
    bill_summary = load_json(os.path.join(work_dir, "bill_summary.json"))

    account_name = args.account_name or disc.get("account_name", "")
    bill_month = disc.get("bill_month", bill_summary.get("bill_month", ""))

    # ── head_map 预构建（F38）──
    head_map = build_head_map(disc)

    # ── 判定 ──
    findings, protected, exempted, other_products, scan_status = run_judgment(
        disc, monitor, lookup, bill_summary, advisor, head_map
    )

    # ── 汇总 ──
    by_product = {}
    total_saving = 0
    for f in findings:
        prod = f.get("product", "其他")
        by_product[prod] = by_product.get(prod, 0) + 1
        s = f.get("saving")
        if s:
            total_saving += s

    # ── step3_results.json ──
    # 生成 insights（智能洞察）
    insights = _build_insights(findings, protected, exempted, other_products, bill_summary, scan_status)

    # monitor_window
    monitor_data_raw = load_json(os.path.join(work_dir, "monitor_summary.json")) if os.path.exists(os.path.join(work_dir, "monitor_summary.json")) else {}
    monitor_window = monitor_data_raw.get("monitor_window", {})
    if not monitor_window:
        # 从 bill_month 推断
        from datetime import timedelta
        try:
            y, m = bill_month.split("-")
            start = f"{bill_month}-01"
            import calendar
            last_day = calendar.monthrange(int(y), int(m))[1]
            end = f"{bill_month}-{last_day:02d}"
            monitor_window = {"start": start, "end": end}
        except Exception:
            monitor_window = {}

    results = {
        "account_name": account_name,
        "scan_date": datetime.now().strftime("%Y-%m-%d"),
        "bill_month": bill_month,
        "monitor_window": monitor_window,
        "advisor_scan_status": scan_status,
        "findings": findings,
        "protected": protected,
        "exempted": exempted,
        "other_products": other_products,
        "insights": insights,
        "totals": {
            "findings_count": len(findings),
            "total_saving": total_saving,
            "by_product": by_product,
            "protected_count": len(protected),
            "exempted_count": len(exempted),
        },
    }

    out_path = os.path.join(work_dir, "step3_results.json")
    with open(out_path, "w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, default=str)

    # ── Layer 3: 输出 unhandled_resources.json 供 LLM 分析 ──
    if other_products:
        unhandled = []
        for op in other_products:
            prod_title = op.get("product_title", "")
            # 从 disc 中收集该产品的实例摘要
            for dp, regions in (disc.get("results") or {}).items():
                if dp != prod_title:
                    continue
                for region, instances in regions.items():
                    if not isinstance(instances, list):
                        continue
                    for inst in instances:
                        rid = (inst.get("InstanceId") or inst.get("ResourceId")
                               or inst.get("DiskId") or "")
                        cost = lookup.get(rid, {}).get("cost", 0)
                        unhandled.append({
                            "product": prod_title,
                            "region": region,
                            "resource_id": rid,
                            "resource_name": inst.get("InstanceName", inst.get("ResourceName", "")),
                            "spec": get_inst_spec(inst, prod_title),
                            "monthly_cost": cost,
                            "charge_type": inst.get("InstanceChargeType",
                                                     inst.get("PayType",
                                                              inst.get("ChargeType", ""))),
                            "state": inst.get("InstanceState",
                                              inst.get("InstanceStatusDesc", "")),
                        })
        if unhandled:
            unhandled_path = os.path.join(work_dir, "unhandled_resources.json")
            with open(unhandled_path, "w", encoding="utf-8") as fp:
                json.dump(unhandled, fp, ensure_ascii=False, default=str, indent=2)
            print(f"  Layer 3: {len(unhandled)} unhandled resources → {unhandled_path}", file=sys.stderr)

    # ── HTML 看板渲染（最终交付物，对齐 billing 模式）──
    html_path = ""
    html_size = 0
    if args.render_html:
        try:
            from render_costopt_html import render_html  # noqa: E402
            uin = bill_summary.get("uin") or disc.get("uin") or "primary"
            today_dir = datetime.now().strftime("%Y-%m-%d")
            # --report-dir 若指定，视为最终落盘目录直接使用（避免 reports/reports 路径重复）；
            # 缺省时按统一约定拼接 $WORKBUDDY_WORKSPACE/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}
            cloud = (getattr(args, "cloud", "") or "tencent").strip()
            project = (getattr(args, "project", "") or "").strip()
            if args.report_dir:
                report_dir = args.report_dir
            else:
                workspace = os.environ.get("WORKBUDDY_WORKSPACE")
                if not workspace:
                    # WORKBUDDY_WORKSPACE 未设置时直接报错退出，
                    # 而非静默回退到插件目录（会导致报告写到 workspace 外，产物面板不可见）。
                    # 主理人在 spawn 时必须 export WORKBUDDY_WORKSPACE，否则视为配置错误。
                    print("❌ FATAL: WORKBUDDY_WORKSPACE environment variable is not set.", file=sys.stderr)
                    print("   Reports cannot be saved to the workspace artifacts panel.", file=sys.stderr)
                    print("   Fix: ensure the team lead exports WORKBUDDY_WORKSPACE before spawning the agent.", file=sys.stderr)
                    sys.exit(1)
                if project:
                    report_dir = os.path.join(workspace, "reports", cloud, project, str(uin), today_dir)
                else:
                    # 单账号退化：reports/{uin}/{date}
                    report_dir = os.path.join(workspace, "reports", str(uin), today_dir)
            os.makedirs(report_dir, exist_ok=True)
            # 命名 v3：用稳定 PROJECT 码而非波动昵称，规避多账号并发重名/覆盖；
            #   en 版由 agent 在 --report-dir 同目录另存 costopt-{cloud}-{project}-{uin}-en.html
            if project:
                fname = f"costopt-{cloud}-{project}-{uin}.html"
            else:
                fname = f"costopt-{cloud}-uin{uin}.html"
            html_path = os.path.join(report_dir, fname)
            # 把账单总额回填给 render（render 用 totals.total_bill_cost）
            render_input = dict(results)
            render_input.setdefault("uin", uin)
            render_input.setdefault("monitor_window", {})
            render_input["totals"] = dict(results["totals"])
            render_input["totals"].setdefault(
                "total_bill_cost", bill_summary.get("total_cost", 0))
            html = render_html(render_input, lang=args.lang)
            with open(html_path, "w", encoding="utf-8") as fp:
                fp.write(html)
            html_size = len(html.encode("utf-8"))
        except Exception as e:
            print(f"[render] HTML 渲染失败: {e}", file=sys.stderr)
            html_path = ""

    # ── stdout 摘要 ──
    print(json.dumps({
        "status": "ok",
        "judgment_ready": True,
        "findings_count": len(findings),
        "total_saving": total_saving,
        "by_product": by_product,
        "protected_count": len(protected),
        "exempted_count": len(exempted),
        "advisor_scan_status": scan_status,
        "output_file": out_path,
        "report_html": html_path,
        "report_html_size": html_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
