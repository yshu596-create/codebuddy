#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
i18n.py -- 团队共享的 zh->en 枚举术语字典 + 正则规则引擎 + API 动态注入(所有 cloudops 报告渲染器共用)

这是 cloudops-team 全体专家(腾讯/华为账单,costopt,auditor,inspector,pptx 等)
的**唯一**数据值翻译来源.任何报告渲染脚本若需要把"标准枚举型"中文数据值
(产品名 / 地域名 / 计费模式 / 规格族 / 账户前缀 / 规则引擎洞察固定话术)转成英文,
都应 `from i18n import tr, _ZH2EN_RAW`,而不是各自复制一份会漂移的字典.

设计:
  * 五层翻译管线(按此顺序执行):
      (1) 正则规则引擎 -- 处理有固定模式的地域名("XX地区(YY)"自动拆解翻译)
      (2) API 动态注入层 -- 利用账单 API 返回的 BusinessCode 自动翻译产品名
      (3) 静态字典精确匹配 -- 处理计费模式,规格族等无规律的枚举值
      (4) 拼音回退 -- 未知城市名自动转拼音(pypinyin 或硬编码映射)
      (5) LLM 翻译钩子(可选)-- 注册后,未翻译的中文字符串交由 LLM 兜底
  * 纯标准库,零外部依赖,import-safe(pypinyin 可选).
  * `tr(s, lang)` 仅在 lang == "en" 时生效;其他语言/空值原样返回.
  * 有序子串替换,运行时按中文长度降序排列,避免"云服务器"先于"云服务器CVM"误替换.
  * 翻译审计:tr() 自动记录所有输出仍含中文的条目(_MISSES),
    渲染脚本可通过 get_translation_report() 获取未翻译条目列表并输出到 JSON.
  * HTML 残留扫描:scan_residual_chinese() 在最终 HTML 上做二次扫描,
    捕获"根本没过 tr()"的遗漏字段,输出未翻译条目列表.

X 红线:`tr()` 绝不用于翻译自定义标签值(head/business/customer 等真实业务名,如
   东风总院/一汽丰田项目/长安汽车/自驾合规/合规SDK),那些必须保持中文原样.
   调用方只能把 `tr()` 用在标准枚举字段(产品名/地域名/计费模式/账户前缀/洞察话术)上.

导出:
  _ZH2EN_RAW       -- 原始 (中文, 英文) 映射列表(可被其他模块扩展/审阅)
  build_terms      -- 把任意映射列表按中文长度降序排好(长串先替换)
  _ZH2EN_TERMS     -- _ZH2EN_RAW 经 build_terms 排序后的默认术语表
  _REGION_ZH_EN    -- 中国大区中文->英文映射(地域正则用)
  _CITY_PINYIN     -- 城市中文名->英文拼音映射(地域翻译回退用)
  _BIZCODE_EN_MAP  -- BusinessCode -> 英文显示名映射(API 动态注入用)
  inject_bizcode_map -- 注入 BusinessCode -> 英文映射(渲染脚本调用)
  set_llm_hook     -- 注册 LLM 翻译钩子(Layer 5 可选兜底)
  _MISSES          -- 未翻译条目收集({原文: 翻译后仍含中文的结果})
  get_translation_report -- 获取翻译审计报告(未翻译条目列表 + 建议字典条目)
  scan_residual_chinese -- 扫描 HTML 残留中文(二次审计,捕获未过 tr() 的遗漏)
  reset_misses     -- 清空未翻译记录(新一轮渲染前调用)
  tr               -- 翻译函数
"""

import json
import os
import re

# ===============================================================================
# 零层:正则规则引擎的数据源(地域模式匹配)
# ===============================================================================

# ---- 中国大区中文->英文映射 ----
_REGION_ZH_EN = {
    "华东": "East China",
    "华南": "South China",
    "华北": "North China",
    "西南": "Southwest China",
    "西北": "Northwest China",
    "东北": "Northeast China",
    "华中": "Central China",
    "东南亚": "Southeast Asia",
    "亚太": "Asia Pacific",
    "北美": "North America",
    "南美": "South America",
    "欧洲": "Europe",
    "非洲": "Africa",
    "中东": "Middle East",
    "其他": "Other",
    # 华为云地域前缀
    "中国": "China",
}

# ---- 城市中文名->英文名映射(常见城市,未知城市回退到 pypinyin 拼音) ----
_CITY_PINYIN = {
    # 中国大陆
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou", "深圳": "Shenzhen",
    "成都": "Chengdu", "重庆": "Chongqing", "南京": "Nanjing", "杭州": "Hangzhou",
    "武汉": "Wuhan", "西安": "Xi'an", "福州": "Fuzhou", "长沙": "Changsha",
    "济南": "Jinan", "青岛": "Qingdao", "大连": "Dalian", "沈阳": "Shenyang",
    "哈尔滨": "Harbin", "长春": "Changchun", "郑州": "Zhengzhou", "昆明": "Kunming",
    "贵阳": "Guiyang", "南宁": "Nanning", "兰州": "Lanzhou", "太原": "Taiyuan",
    "合肥": "Hefei", "南昌": "Nanchang", "石家庄": "Shijiazhuang", "天津": "Tianjin",
    "中卫": "Zhongwei", "乌兰察布": "Ulanqab", "保定": "Baoding", "苏州": "Suzhou",
    "无锡": "Wuxi", "宁波": "Ningbo", "厦门": "Xiamen", "珠海": "Zhuhai",
    "东莞": "Dongguan", "佛山": "Foshan", "惠州": "Huizhou", "中山": "Zhongshan",
    "江门": "Jiangmen", "绵阳": "Mianyang", "拉萨": "Lhasa", "呼和浩特": "Hohhot",
    "银川": "Yinchuan", "西宁": "Xining", "海口": "Haikou", "三亚": "Sanya",
    # 中国香港/澳门/台湾
    "香港": "Hong Kong", "澳门": "Macao", "台北": "Taipei",
    # 海外
    "新加坡": "Singapore", "曼谷": "Bangkok", "首尔": "Seoul", "东京": "Tokyo",
    "孟买": "Mumbai", "法兰克福": "Frankfurt", "莫斯科": "Moscow", "多伦多": "Toronto",
    "硅谷": "Silicon Valley", "弗吉尼亚": "Virginia", "圣保罗": "São Paulo",
    "雅加达": "Jakarta", "吉隆坡": "Kuala Lumpur", "马尼拉": "Manila",
    "迪拜": "Dubai", "约翰内斯堡": "Johannesburg",
    # 腾讯云专属集群地域(自动驾驶云 ADC)
    "上海自动驾驶云": "Shanghai ADC", "广州自动驾驶云": "Guangzhou ADC",
    "北京自动驾驶云": "Beijing ADC", "深圳自动驾驶云": "Shenzhen ADC",
    "其他": "Other",
}

# ===============================================================================
# 0.5 层:API 动态注入 -- BusinessCode -> 英文显示名
# ===============================================================================
# 腾讯云账单 API 返回 BusinessCode(如 p_emr, p_cvm, p_wsm_waf),
# 这是稳定的产品标识符.我们维护一份 BusinessCode -> 英文显示名 映射,
# 渲染脚本调用 inject_bizcode_map() 把 step3_data.json 里的
# product_code_to_name 中的中文名->BusinessCode 反查注入到 tr() 里.
#
# 这样:
#   * 已知 BusinessCode -> 精确英文名(如 p_emr -> EMR)
#   * 未知 BusinessCode -> 回退到静态字典
#   * 两者都不知道 -> 保留中文名
#
# 新产品只要在 _BIZCODE_EN_MAP 里加一条映射,tr() 就能自动翻译.
# 不在映射里的 BusinessCode 不会影响已有逻辑.

_BIZCODE_EN_MAP = {
    # 腾讯云 -- 计算与网络
    "p_cvm": "CVM",
    "p_cbs": "CBS",
    "p_clb": "CLB",
    "p_eip": "EIP",
    "p_nat": "NAT Gateway",
    "p_vpn": "VPN Gateway",
    "p_ccn": "CCN",
    "p_lighthouse": "Lighthouse",
    "p_bm": "BM",
    # 腾讯云 -- 存储
    "p_cos": "COS",
    "p_cfs": "CFS",
    "p_goosefs": "GooseFS",
    # 腾讯云 -- 数据库
    "p_cdb": "TencentDB MySQL",
    "p_postgresql": "TencentDB PostgreSQL",
    "p_mongodb": "TencentDB MongoDB",
    "p_redis": "TencentDB for Redis",
    "p_cynosdb": "TDSQL-C",
    "p_mariadb": "TencentDB MariaDB",
    "p_sqlserver": "TencentDB SQL Server",
    # 腾讯云 -- 大数据
    "p_emr": "EMR",
    "p_oceanus": "Oceanus",
    "p_dlc": "Data Lake Compute",
    "p_cdwch": "TCHouse-C",
    "p_cdwdoris": "TCHouse-D",
    "p_es": "Elasticsearch Service",
    "p_ckafka": "CKafka",
    "p_dts": "DTS",
    # 腾讯云 -- 容器与微服务
    "p_011691": "TKE",   # BusinessCode for TKE
    "p_tcr": "TCR",
    "p_tse_sre": "TSE",
    "p_tdmq": "TDMQ",
    # 腾讯云 -- 安全
    "p_wsm_waf": "T-Sec-WAF",
    "p_kms": "T-Sec-KMS",
    "p_ssm": "T-Sec-Secrets Manager",
    "p_cloudfirewall": "T-Sec-Cloud Firewall",
    "p_cwp": "T-Sec-CWP",
    "p_csip": "T-Sec-CSIP",
    "p_ddos": "T-Sec-DDoS",
    "p_ssl": "SSL Certificate",
    "p_cds": "T-Sec-Data Security",
    "p_dsa": "T-Sec-DSA",
    # 腾讯云 -- 监控与可观测
    "p_cm": "Cloud Monitor",
    "p_cls": "CLS",
    "p_apm": "APM",
    "p_cat": "CAT",
    # 腾讯云 -- AI 与开发
    "p_tio": "TI Platform",
    "p_tcaca": "Coding Assistant",
    "p_tcllmke": "Agent Platform",
    "p_bi": "Cloud BI",
    "p_lke": "Knowledge Engine",
    "p_hunyuan": "Hunyuan",
    "p_ti": "TI Platform",
    # 腾讯云 -- 网络与CDN
    "p_cdn": "CDN",
    "p_eoa": "EdgeOne",
    "p_dsa": "DSA",
    "p_dc": "Direct Connect",
    "p_scf": "SCF",
    "p_ci": "Cloud Infinite",
    "p_snapshot": "CBS Snapshot",
    "p_iot": "IoT Hub",
    "p_ses": "SES",
    # 腾讯云 -- 数据库(扩展)
    "p_sqlserver": "TencentDB SQL Server",
    "p_cynosdb": "TDSQL-C",
    "p_mariadb": "TencentDB MariaDB",
    "p_tdsql": "TDSQL",
    "p_dbbrain": "DBBrain",
    "p_dts": "DTS",
    # 腾讯云 -- 大数据(扩展)
    "p_emr": "EMR",
    "p_oceanus": "Oceanus",
    "p_dlc": "Data Lake Compute",
    "p_cdwch": "TCHouse-C",
    "p_cdwdoris": "TCHouse-D",
    "p_es": "Elasticsearch Service",
    "p_ckafka": "CKafka",
    "p_dlc": "Data Lake Compute",
    "p_wedata": "WeData",
    # 腾讯云 -- 容器与微服务(扩展)
    "p_011691": "TKE",
    "p_tcr": "TCR",
    "p_tse_sre": "TSE",
    "p_tdmq": "TDMQ",
    "p_eks": "EKS",
    "p_tcaplusdb": "TcaplusDB",
    # 腾讯云 -- 中间件与微服务
    "p_apisix": "API Gateway",
    "p_cam": "CAM",
    "p_ssmp": "SSO",
    # 腾讯云 -- 视频与媒体
    "p_vod": "VOD",
    "p_live": "LVB",
    "p_trtc": "TRTC",
    "p_im": "IM",
    # 腾讯云 -- 其他
    "p_scf": "SCF",
    "p_ci": "Cloud Infinite",
    "p_snapshot": "CBS Snapshot",
    "p_iot": "IoT Hub",
    "p_ses": "SES",
    "p_lighthouse": "Lighthouse",
    "p_bm": "BM",
    "p_goosefs": "GooseFS",
    "p_ckafka": "CKafka",
    "p_dts": "DTS",
    "p_tcr": "TCR",
    "p_wsm_waf": "T-Sec-WAF",
    "p_kms": "T-Sec-KMS",
    "p_ssm": "T-Sec-Secrets Manager",
    "p_cloudfirewall": "T-Sec-Cloud Firewall",
    "p_cwp": "T-Sec-CWP",
    "p_csip": "T-Sec-CSIP",
    "p_ddos": "T-Sec-DDoS",
    "p_ssl": "SSL Certificate",
    # 腾讯云 -- 游戏
    "p_gse": "GSE",
    "p_mgmt": "Game Management",
    # 腾讯云 -- 数据治理
    "p_wedata": "WeData",
    "p_ei": "Data Integration",
    # 华为云 -- 对应产品(BusinessCode 命名可能不同)
    "h_ecs": "ECS",
    "h_evs": "EVS",
    "h_obs": "OBS",
    "h_vpc": "VPC",
}

# 运行时动态注入的 {中文名: 英文名} 映射(由 inject_bizcode_map() 填充)
_DYNAMIC_ZH_EN = {}


def inject_bizcode_map(product_code_to_name):
    """从 step3_data.json 的 product_code_to_name 动态注入翻译映射.

    product_code_to_name 格式: {"p_emr": "弹性MapReduce", "p_cvm": "云服务器CVM", ...}

    工作流程:
    1. 遍历 product_code_to_name,对每个 (code, zh_name) 对:
       - 如果 code 在 _BIZCODE_EN_MAP 中 -> 取英文显示名
       - 否则跳过(回退到静态字典或保留中文)
    2. 将 (zh_name, en_name) 对注入到 _DYNAMIC_ZH_EN
    3. 重新构建 _ZH2EN_TERMS(合并静态 + 动态)

    调用时机:render_billing_html.py 的 build_data() 或 main() 中,
    在任何 tr() 调用之前调用一次即可.
    """
    global _ZH2EN_TERMS, _DYNAMIC_ZH_EN
    new_entries = {}
    for code, zh_name in (product_code_to_name or {}).items():
        if not code or not zh_name:
            continue
        en_name = _BIZCODE_EN_MAP.get(code)
        if en_name:
            new_entries[zh_name] = en_name
    if new_entries:
        _DYNAMIC_ZH_EN.update(new_entries)
        # 合并静态 + 动态,重建排序表
        combined = list(_ZH2EN_RAW) + [(k, v) for k, v in new_entries.items()]
        _ZH2EN_TERMS = build_terms(combined)


# ===============================================================================
# 第一层:静态字典(精确匹配)
# ===============================================================================
# 注:地域名由零层正则引擎自动处理.
#     产品名优先走 0.5 层 API 动态注入,静态字典只做兜底.
_ZH2EN_RAW = [
    # ---- 腾讯云 T-Sec 产品线:带括号后缀的完整名优先(避免短键吃掉长键) ----
    ("T-Sec-Web应用防火墙(WAF)", "T-Sec-WAF"),
    ("T-Sec-密钥管理系统(KMS)", "T-Sec-KMS"),
    ("T-Sec-凭据管理系统(SSM)", "T-Sec-Secrets Manager (SSM)"),
    ("T-Sec-云防火墙(CFW)", "T-Sec-Cloud Firewall (CFW)"),
    ("T-Sec-Web应用防火墙", "T-Sec-Web Application Firewall"),
    ("T-Sec-密钥管理系统", "T-Sec-KMS"),
    ("T-Sec-凭据管理系统", "T-Sec-Secrets Manager"),
    ("T-Sec-云防火墙", "T-Sec-Cloud Firewall"),
    ("Web应用防火墙", "Web Application Firewall"),
    ("应用防火墙", "Application Firewall"),
    ("密钥管理系统", "KMS"),
    ("凭据管理系统", "Secrets Manager"),
    ("云防火墙", "Cloud Firewall"),
    # ---- 腾讯云产品:带括号/后缀的完整 API 名优先 ----
    ("腾讯云数据仓库 TCHouse-C", "Tencent Cloud Data Warehouse TCHouse-C"),
    ("腾讯云分布式缓存数据库-集群架构", "TencentDB for Redis - Cluster Arch"),
    ("腾讯云分布式缓存数据库-标准架构", "TencentDB for Redis - Standard Arch"),
    ("腾讯云分布式缓存数据库", "TencentDB for Redis"),
    ("腾讯云代码助手", "Tencent Cloud Coding Assistant"),
    ("腾讯云智能体开发平台", "Tencent Cloud Agent Platform"),
    ("腾讯云小微数智人", "Tencent Cloud Digital Human"),
    ("腾讯云语音识别", "Tencent Cloud ASR"),
    ("腾讯安全天御-文本内容安全", "T-Sec Text Content Security"),
    ("T-Sec-腾讯安全天御-文本内容安全", "T-Sec Text Content Security"),
    ("腾讯云数据仓库 TCHouse-D", "Tencent Cloud Data Warehouse TCHouse-D"),
    ("腾讯云BI", "Tencent Cloud BI"),
    ("腾讯云TI平台TI-ONE", "Tencent TI Platform TI-ONE"),
    ("弹性MapReduce", "EMR"),
    ("流计算Oceanus", "Oceanus"),
    ("TDSQL-C MySQL版", "TDSQL-C MySQL Edition"),
    ("数据万象CI", "Cloud Infinite"),
    ("TSE 注册配置中心", "TSE Registry & Config Center"),
    ("云监控", "Cloud Monitor"),
    # ---- 腾讯云产品:YX 项目发现的缺失翻译 ----
    ("云联络中心", "Cloud Contact Center"),
    ("TSE 云原生APIGateway", "TSE Cloud Native API Gateway"),
    ("联网搜索API", "Web Search API"),
    ("智能媒资托管", "Smart Media Asset Management"),
    ("短信SMS", "SMS"),
    ("应用观测", "Application Observability"),
    ("图像分析TIIA", "Image Analysis TIIA"),
    ("对话原子能力DeepSeek", "DeepSeek Dialog Capability"),
    ("实时语音识别", "Real-time ASR"),
    ("录音文件识别", "Recording File ASR"),
    ("云小微数字人线上服务包_2D交互日包", "Digital Human 2D Interactive Daily Pack"),
    ("云小微数字人线上服务包", "Digital Human Online Service Pack"),
    ("按月订阅费", "Monthly Subscription"),
    ("锐驰型", "Ruichi Type"),
    ("增强型SSD CBS", "Enhanced SSD CBS"),
    ("增强型SSD云硬盘", "Enhanced SSD CBS"),
    ("增强型", "Enhanced"),
    ("点播媒体处理", "VOD Media Processing"),
    ("点播-按日结算", "VOD - Daily Settlement"),
    ("点播", "VOD"),
    ("中国境内", "China Mainland"),
    ("境内", "Mainland"),
    ("资源包", "Resource Pack"),
    ("免费资源包MaaS", "Free Resource Pack MaaS"),
    ("免费资源包", "Free Resource Pack"),
    ("云原生API网关-标准版", "Cloud Native API Gateway - Standard Edition"),
    ("云原生API网关", "Cloud Native API Gateway"),
    ("云原生", "Cloud Native"),
    ("监控服务", "Monitor Service"),
    ("Prometheus监控服务", "Prometheus Monitor Service"),
    ("交互日包", "Interactive Daily Pack"),
    ("企业版", "Enterprise Edition"),
    ("企业", "Enterprise"),
    ("录音文件识别极速版", "Recording File ASR Fast Edition"),
    ("录音文件识别极速", "Recording File ASR Fast"),
    ("按日结算", "Daily Settlement"),
    ("通信套餐包", "Communication Package"),
    ("增值功能套餐包", "Value-added Feature Package"),
    ("云产品", "Cloud Product"),
    ("云数据库 MySQL 备份-按量计费", "TencentDB MySQL Backup - Pay-as-you-go"),
    ("云数据库 MySQL 备份", "TencentDB MySQL Backup"),
    ("腾讯安全天御", "T-Sec"),
    ("文本内容安全", "Text Content Security"),
    ("图像分析", "Image Analysis"),
    # ---- 已有产品(保留,排序由 build_terms 保证) ----
    ("云联网CCN入流量处理费", "CCN inbound traffic fee"),
    ("云联网CCN-流量计费", "CCN Traffic-based"),
    ("云联网网络连接实例费", "CCN connection fee"),
    ("云联网CCN", "CCN"),
    ("云数据库TencentDB for MongoDB", "TencentDB for MongoDB"),
    ("云数据库MySQL", "TencentDB MySQL"),
    ("云数据库PostgreSQL", "TencentDB PostgreSQL"),
    ("文件存储-通用性能型", "CFS - General Performance"),
    ("文件存储CFS", "CFS"),
    ("高性能云硬盘", "High-Performance CBS"),
    ("通用型SSD云硬盘", "General Purpose SSD CBS"),
    ("SSD云硬盘", "SSD CBS"),
    ("云硬盘快照Snapshot", "CBS Snapshot"),
    ("云硬盘CBS", "CBS"),
    ("负载均衡 CLB", "CLB"),
    ("负载均衡", "CLB"),
    ("消息队列 TDMQ Pulsar 版", "TDMQ Pulsar Edition"),
    ("消息队列 TDMQ", "TDMQ"),
    ("消息服务CKafka", "CKafka"),
    ("Serverless 容器服务", "Serverless TKE"),
    ("容器服务 TKE", "TKE"),
    ("容器服务TKE", "TKE"),
    ("容器镜像服务 TCR", "TCR"),
    ("超级节点", "Super Node"),
    ("日志服务CLS", "CLS"),
    ("专线接入-端口月租费", "Direct Connect - port monthly fee"),
    ("专线接入", "Direct Connect"),
    ("私有域解析 Private DNS", "Private DNS"),
    ("私有连接", "Private Link"),
    ("主机安全", "Host Security"),
    ("堡垒机", "Bastion Host"),
    ("COS 标准存储", "COS Standard Storage"),
    ("COS 对象存储", "COS Object Storage"),
    ("对象存储", "Object Storage"),
    ("标准存储", "Standard Storage"),
    ("云服务器CVM", "CVM"),
    ("云函数SCF", "SCF"),
    ("公网 IP", "Public IP"),
    ("公网IP", "Public IP"),
    ("NAT网关", "NAT Gateway"),
    ("VPN网关", "VPN Gateway"),
    ("资源组预付费", "Resource pack (prepaid)"),
    ("向量数据库", "Vector Database"),
    ("云桌面", "Cloud Desktop"),
    ("数据湖计算", "Data Lake Compute"),
    ("建图大模型", "Mapping LLM"),
    ("月度计费精度差异", "Monthly billing precision diff"),
    # ---- 华为云服务名 ----
    ("弹性云服务器", "ECS"),
    ("对象存储服务", "OBS"),
    ("云硬盘 EVS", "EVS"),
    ("云硬盘EVS", "EVS"),
    ("弹性云硬盘", "EVS"),
    ("虚拟私有云", "VPC"),
    ("弹性负载均衡", "ELB"),
    ("弹性公网IP", "EIP"),
    ("弹性公网 IP", "EIP"),
    ("云数据库 GaussDB", "GaussDB"),
    ("云数据库GaussDB", "GaussDB"),
    ("关系型数据库", "RDS"),
    ("文档数据库服务", "DDS"),
    ("分布式缓存服务", "DCS"),
    ("云容器引擎", "CCE"),
    ("内容分发网络", "CDN"),
    ("数据复制服务", "DRS"),
    ("数据仓库服务", "DWS"),
    ("企业项目", "Enterprise Project"),
    ("公网带宽", "Public Bandwidth"),
    ("共享带宽", "Shared Bandwidth"),
    # ---- 规格族 ----
    ("GPU计算型", "GPU Compute"),
    ("通用性能型", "General Performance"),
    ("通用型", "General Purpose"),
    ("计算型", "Compute Optimized"),
    ("标准型", "Standard"),
    ("白金", "Platinum"),
    ("标准架构", "Standard Arch"),
    ("集群架构", "Cluster Arch"),
    # ---- 计费模式 ----
    ("入流量处理费", "Inbound traffic fee"),
    ("按量计费", "Pay-as-you-go"),
    ("后付费", "Pay-as-you-go"),
    ("包年包月", "Subscription"),
    ("流量计费", "Traffic-based"),
    ("预付费", "Prepaid"),
    # ---- 规格族 / SKU 描述词 ----
    ("智能型", "AI-Optimized"),
    ("旗舰版", "Flagship Edition"),
    ("旗舰", "Flagship"),
    ("标准版", "Standard Edition"),
    # ---- 产品名碎片 ----
    ("精调知识大模型高级", "Fine-tuned Knowledge LLM Advanced"),
    ("大模型高级", "LLM Advanced"),
    ("云硬盘快照", "CBS Snapshot"),
    ("数据湖存储", "Data Lake Storage"),
    ("云硬盘", "CBS"),
    # ---- 残留通用词(放最后,避免吃掉上面的复合词) ----
    ("网关", "Gateway"),
    ("版", " Edition"),
    # ---- 华为云地域(静态字典,正则兜底不到的) ----
    ("华北-北京四", "North China (Beijing-4)"),
    ("华北-北京一", "North China (Beijing-1)"),
    ("华北-乌兰察布一", "North China (Ulanqab-1)"),
    ("华东-上海一", "East China (Shanghai-1)"),
    ("华东-上海二", "East China (Shanghai-2)"),
    ("华南-广州", "South China (Guangzhou)"),
    ("西南-贵阳一", "Southwest China (Guiyang-1)"),
    ("亚太-新加坡", "Asia Pacific (Singapore)"),
    ("亚太-曼谷", "Asia Pacific (Bangkok)"),
    ("北京四", "Beijing-4"),
    ("北京一", "Beijing-1"),
    ("上海一", "Shanghai-1"),
    ("上海二", "Shanghai-2"),
    ("乌兰察布一", "Ulanqab-1"),
    ("贵阳一", "Guiyang-1"),
    # ---- 账户前缀 ----
    ("主账号", "Primary Account"),
    # ---- 规则引擎洞察固定话术 ----
    ("单产品高share比", "Single Product High Share"),
    ("单产品高占比", "Single Product High Share"),
    ("建议拆分负载", "Consider workload splitting"),
    ("建议拆分", "Consider splitting"),
    ("单地域高度集中", "Single-Region High Concentration"),
    ("未享受折扣的产品", "Zero-discount products"),
    ("单点风险", "single-point risk"),
    ("个产品", " products"),
    ("/月", "/month"),
    ("占", "share"),
]


def build_terms(raw=None):
    """把映射列表按中文(key)长度降序排好:长串先替换,杜绝部分命中."""
    return sorted(raw if raw is not None else _ZH2EN_RAW,
                  key=lambda kv: len(kv[0]), reverse=True)


# 运行时按中文长度降序排列:长串先替换,杜绝部分命中
_ZH2EN_TERMS = build_terms(_ZH2EN_RAW)


def _try_pinyin(city_zh):
    """对未知城市名尝试拼音回退.优先用 pypinyin,否则返回原字."""
    if not city_zh:
        return city_zh
    try:
        from pypinyin import pinyin as _pinyin, Style
        parts = _pinyin(city_zh, style=Style.NORMAL)
        return ''.join(p[0].capitalize() for p in parts)
    except ImportError:
        pass
    return city_zh


# 预编译地域正则
# 支持全角括号（）和半角括号()，腾讯云 API 返回的地域名使用全角括号
_RE_TENCENT_REGION = re.compile(
    r'([\u4e00-\u9fff]+)地区[（(]([\u4e00-\u9fffA-Za-z0-9]+)[）)]'
)
# 华为云地域格式 "方位词-城市[序号]"(如 华北-北京四 / 华东-上海一).
# region 段必须是已知方位词之一,避免误吃 "文件存储-通用性能型" 这类含连字符的产品名.
_RE_HUAWEI_REGION = re.compile(
    r'(华东|华南|华北|华中|西南|西北|东北|东南亚|亚太|北美|南美|欧洲|非洲|中东|中国)'
    r'-([\u4e00-\u9fff]+?)([一二三四五六七八九十]*)(?![\u4e00-\u9fff])'
)
_CN_NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
           "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}


# ===============================================================================
# 翻译审计:未翻译条目收集 + LLM 钩子
# ===============================================================================

# 运行时收集:{原始输入: tr() 输出仍含中文的结果}
_MISSES: dict[str, str] = {}

# LLM 翻译钩子(Layer 5 可选兜底)
# 签名: (chinese_text: str) -> str | None
# 返回翻译后的英文,或 None 表示放弃翻译.
_LLM_HOOK = None


def set_llm_hook(hook):
    """注册 LLM 翻译钩子.传入 None 可取消.

    hook 签名: (chinese_text: str) -> str | None
    - 输入:仍含中文字符的字符串
    - 输出:翻译后的英文,或 None(放弃,保留中文)
    - 钩子不应抛出异常;出错时应返回 None

    用法示例(渲染脚本中):
        from i18n import set_llm_hook, create_llm_hook
        set_llm_hook(create_llm_hook())
    """
    global _LLM_HOOK
    _LLM_HOOK = hook


# -- LLM 钩子持久缓存路径 --
_LLM_CACHE_PATH = os.path.expanduser("~/.workbuddy/i18n-llm-cache.json")
# 运行时内存缓存(避免同一轮渲染重复读文件)
_LLM_CACHE_MEM: dict[str, str] = {}


def _load_llm_cache() -> dict[str, str]:
    """加载持久缓存(首次调用读文件,后续用内存缓存)."""
    if _LLM_CACHE_MEM:
        return _LLM_CACHE_MEM
    try:
        with open(_LLM_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _LLM_CACHE_MEM.update(data)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _LLM_CACHE_MEM


def _save_llm_cache():
    """将内存缓存持久化到磁盘."""
    try:
        os.makedirs(os.path.dirname(_LLM_CACHE_PATH), exist_ok=True)
        with open(_LLM_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_LLM_CACHE_MEM, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # 磁盘写入失败时静默


def _call_llm_api(text: str) -> str | None:
    """通过 HTTP 调用 LLM API 翻译中->英.

    支持两种环境变量配置:
      1) I18N_LLM_URL + I18N_LLM_KEY  -- 自定义 OpenAI 兼容端点
      2) OPENAI_BASE_URL + OPENAI_API_KEY -- 标准 OpenAI 配置

    返回翻译结果或 None.
    """
    import urllib.request
    import urllib.error

    url = os.environ.get("I18N_LLM_URL") or os.environ.get("OPENAI_BASE_URL")
    api_key = os.environ.get("I18N_LLM_KEY") or os.environ.get("OPENAI_API_KEY")
    if not url or not api_key:
        return None

    # 规范化 URL:补齐 /v1/chat/completions 路径
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/v1/chat/completions"

    model = os.environ.get("I18N_LLM_MODEL", "gpt-4o-mini")

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "Translate the following Chinese text to English. Output ONLY the English translation, nothing else. Do not add explanations or quotes."},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 256,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        result = body.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        # 安全校验:结果不能含中文,不能为空
        if result and not re.search(r"[\u4e00-\u9fff]", result):
            return result
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, OSError, TimeoutError):
        pass
    return None


def create_llm_hook():
    """创建带持久缓存的 LLM 翻译钩子.

    工作流程:
      1. 查内存/磁盘缓存 -> 命中则直接返回
      2. 调 LLM API -> 成功则写缓存 + 返回
      3. API 不可用 -> 返回 None(保留中文)

    使用方式:
        from i18n import set_llm_hook, create_llm_hook
        set_llm_hook(create_llm_hook())

    安全:仅翻译标准枚举型文本(产品名/地域名/洞察话术等),
    不翻译自定义标签值(由 tr() 调用方保证不把自定义标签传给 tr).
    """
    cache = _load_llm_cache()

    def hook(chinese_text: str) -> str | None:
        # (1) 查缓存
        if chinese_text in cache:
            return cache[chinese_text]
        # (2) 调 LLM API
        result = _call_llm_api(chinese_text)
        if result:
            cache[chinese_text] = result
            _LLM_CACHE_MEM[chinese_text] = result
            # 延迟持久化:不在每次翻译都写磁盘,由 _flush_on_miss() 批量写
            return result
        return None

    return hook


def _flush_llm_cache():
    """将本轮新增的缓存条目持久化到磁盘.在渲染结束时调用."""
    if _LLM_CACHE_MEM:
        _save_llm_cache()


def reset_misses():
    """清空未翻译记录.在新一轮渲染开始前调用."""
    _MISSES.clear()


def get_translation_report():
    """获取翻译审计报告.

    返回 dict:
        miss_count: int           -- 未翻译条目数
        misses: list[dict]         -- 未翻译条目列表
            [{input: str, output: str, chinese_segments: list[str]}]
        suggested_entries: list    -- 建议添加的字典条目
            [{chinese: str, suggested_english: str, source: str}]
    """
    import json
    misses = []
    suggested = []

    for original, translated in _MISSES.items():
        # 提取残留的中文字符段
        cn_segs = re.findall(r'[\u4e00-\u9fff]+', translated)
        misses.append({
            "input": original,
            "output": translated,
            "chinese_segments": cn_segs,
        })
        # 为每个残留中文字段生成建议字典条目
        for seg in cn_segs:
            # 尝试用 pypinyin 生成拼音作为建议
            pinyin_hint = _try_pinyin(seg) if seg != _try_pinyin(seg) else ""
            suggested.append({
                "chinese": seg,
                "suggested_english": pinyin_hint or f"<TODO: translate '{seg}'>",
                "source": original,
            })

    # 去重 suggested
    seen = set()
    deduped = []
    for s in suggested:
        key = s["chinese"]
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    return {
        "miss_count": len(misses),
        "misses": misses,
        "suggested_entries": deduped,
    }


def scan_residual_chinese(html, lang="zh"):
    """扫描最终 HTML 中的残留中文字符,返回未翻译条目列表.

    与 get_translation_report() 互补:
      * get_translation_report() 审计的是 tr() 调用层----只捕获"调了 tr() 但没翻干净"的条目
      * scan_residual_chinese() 审计的是 HTML 渲染层----捕获"根本没过 tr()"的遗漏

    仅在 lang == "en" 时生效,其他语言返回空列表.

    返回: [
        {
            "text": "残留中文片段",
            "count": 出现次数,
            "contexts": ["上下文1", ...],  # 最多3个,前后各40字符
            "category": "resource_type" | "product_name" | "region" | "unknown"
        },
        ...
    ]
    """
    if lang != "en" or not html:
        return []

    # 1) 剥离外部 <script src="..."> 标签(CDN 引入,不含数据)
    #    保留内联 <script> 块(含 JSON payload 如 const DATA = {...})
    cleaned = re.sub(r'<script[^>]*\bsrc\s*=\s*["\'][^"\']*["\'][^>]*>\s*</script>',
                     '', html, flags=re.IGNORECASE)
    # 剥离 <style> 块(CSS 中的中文不算遗漏)
    cleaned = re.sub(r'<style[^>]*>.*?</style>', '', cleaned, flags=re.DOTALL | re.IGNORECASE)

    # 2) 剥离 HTML 标签,只保留文本节点 + 内联 script 中的字符串
    text_only = re.sub(r'<[^>]+>', ' ', cleaned)

    # 2.5) 过滤 JS 注释(// 开头的行含中文注释不算遗漏)
    #      只保留引号内的中文(JSON 字符串值),去掉注释中的中文
    lines = text_only.split('\n')
    filtered_lines = []
    for line in lines:
        stripped = line.strip()
        # 纯注释行 -> 跳过
        if stripped.startswith('//'):
            continue
        # 行内注释:截掉 // 之后的部分
        comment_idx = stripped.find('//')
        if comment_idx > 0:
            stripped = stripped[:comment_idx]
        filtered_lines.append(stripped)
    text_only = '\n'.join(filtered_lines)

    # 3) 收集所有中文片段(>=2 个连续中文字符)
    zh_runs = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{2,}', text_only)

    # 4) 已知不翻译的模式(技术标识,业务自定义名)
    #    注意:\w 在 Python 3 匹配 Unicode(含中文),所以用 [a-zA-Z0-9_] 替代
    _skip_patterns = [
        re.compile(r'^[a-zA-Z0-9_.\-@/]+$'),  # 纯技术标识 (cam-add_user, 175.24.x.x)
        re.compile(r'^\d{4}-\d{2}-\d{2}'),      # 日期开头
        re.compile(r'^ins-[a-f0-9]+$'),          # 资源 ID
        re.compile(r'^req-[a-f0-9]+$'),          # RequestId
        re.compile(r'^\d+$'),                    # 纯数字
    ]

    # 5) 去重 + 计数 + 附带上文
    seen = {}  # text -> {count, contexts, category}
    for run in zh_runs:
        run = run.strip()
        if not run or len(run) < 2:
            continue
        # 跳过已知不翻译的模式
        if any(pat.match(run) for pat in _skip_patterns):
            continue

        cat = _classify_zh_run(run)
        if run not in seen:
            ctx = _extract_context(text_only, run)
            seen[run] = {"count": 1, "contexts": ctx, "category": cat}
        else:
            seen[run]["count"] += 1

    # 6) 排序:按出现次数降序
    return [
        {
            "text": text,
            "count": info["count"],
            "contexts": info["contexts"],
            "category": info["category"],
        }
        for text, info in sorted(seen.items(), key=lambda kv: -kv[1]["count"])
    ]


def _classify_zh_run(text):
    """推断残留中文片段的分类,便于维护者定向补充字典."""
    if any(kw in text for kw in ["地区", "华东", "华南", "华北", "西南", "西北"]):
        return "region"
    if any(kw in text for kw in ["云", "服务", "数据库", "存储", "计算", "网络",
                                  "安全", "监控", "容器", "函数", "网关"]):
        return "product_name"
    if 2 <= len(text) <= 8 and any(kw in text for kw in
        ["器", "组", "策略", "规则", "密钥", "用户", "角色", "证书", "实例"]):
        return "resource_type"
    return "unknown"


def _extract_context(full_text, snippet, max_contexts=3, window=40):
    """从全文中提取 snippet 出现的上下文(前后各 window 字符)."""
    contexts = []
    start = 0
    while len(contexts) < max_contexts:
        idx = full_text.find(snippet, start)
        if idx == -1:
            break
        ctx_start = max(0, idx - window)
        ctx_end = min(len(full_text), idx + len(snippet) + window)
        ctx = full_text[ctx_start:ctx_end].strip()
        ctx = re.sub(r'\s+', ' ', ctx)
        contexts.append(ctx)
        start = idx + len(snippet)
    return contexts


def tr(s, lang="zh"):
    """翻译标准枚举型数据值(产品名/地域名/账户前缀/洞察话术).

    五层管线(按此顺序执行):
      (1) 正则规则引擎 -- 处理有固定模式的地域名
      (2) API 动态注入层 -- BusinessCode 反查英文名(需先调用 inject_bizcode_map)
      (3) 静态字典精确匹配 -- 处理计费模式,规格族等
      (4) 拼音回退 -- 未知城市名自动转拼音
      (5) LLM 翻译钩子 -- 可选兜底(需先调用 set_llm_hook 注册)

    X 禁止用于自定义标签值(业务/客户名)----那些保持中文.
    lang != 'en' 或空值时原样返回.
    """
    if lang != "en" or not s:
        return s
    out = str(s)

    # -- (1) 正则规则引擎(地域名)--
    def _tencent_region_replacer(m):
        region_zh = m.group(1)
        city_zh = m.group(2)
        region_en = _REGION_ZH_EN.get(region_zh, region_zh)
        city_en = _CITY_PINYIN.get(city_zh, _try_pinyin(city_zh))
        return f"{region_en} ({city_en})"

    out = _RE_TENCENT_REGION.sub(_tencent_region_replacer, out)

    def _huawei_region_replacer(m):
        region_zh = m.group(1)
        city_zh = m.group(2)
        suffix_zh = m.group(3)
        region_en = _REGION_ZH_EN.get(region_zh, region_zh)
        city_en = _CITY_PINYIN.get(city_zh, _try_pinyin(city_zh))
        suffix = ''.join(_CN_NUM.get(c, c) for c in suffix_zh)
        return f"{region_en} ({city_en}-{suffix})" if suffix else f"{region_en} ({city_en})"

    out = _RE_HUAWEI_REGION.sub(_huawei_region_replacer, out)

    # -- (2) 静态 + 动态字典精确替换 --
    # _ZH2EN_TERMS 已在 inject_bizcode_map() 中合并了动态条目
    for zh, en in _ZH2EN_TERMS:
        if zh in out:
            out = out.replace(zh, en)

    # -- (2).5 规格格式正则替换 --
    # 处理 "X核YGB" / "X核YGB -> Z核WGB" / "降至X核YGB" 等规格描述格式
    # 必须在字典替换之后执行,因为字典可能已将"降至"替换为"Downscale to "
    out = re.sub(r'(\d+)\s*核\s*(\d+(?:\.\d+)?)\s*GB', r'\1 vCPU \2 GB', out)
    out = re.sub(r'(\d+)\s*核\s*(\d+(?:\.\d+)?)\s*Mbps', r'\1 vCPU \2 Mbps', out)

    # -- 后处理:全角标点归一化 + 空格折叠 --
    out = out.replace("(", " (").replace(")", ")").replace(",", ", ").replace(",", ", ")
    while "  " in out:
        out = out.replace("  ", " ")
    out = out.strip()

    # -- (5) LLM 翻译钩子(可选兜底) --
    _has_chinese = bool(re.search(r'[\u4e00-\u9fff]', out))
    if _has_chinese and _LLM_HOOK is not None:
        try:
            llm_result = _LLM_HOOK(out)
            if llm_result and isinstance(llm_result, str) and not re.search(r'[\u4e00-\u9fff]', llm_result):
                out = llm_result
                _has_chinese = False
        except Exception:
            pass  # LLM 钩子出错时静默回退

    # -- 翻译审计:记录仍含中文的条目 --
    # 两种情况都记录:(1) 部分翻译但仍有残留 (2) 完全未翻译
    if _has_chinese:
        _MISSES[str(s)] = out

    return out
