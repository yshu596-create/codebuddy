# CODEBUDDY.md

This file provides guidance to CodeBuddy Code when working with code in this repository.

## What this repository is

This is the source for **`cloudops-team-pro`** (v2.0.0), a **CodeBuddy plugin package** — an AIOps cloud-operations expert team. It is *not* a standalone application; it is a plugin distributed via the CodeBuddy marketplace mechanism and installed into `~/.workbuddy/plugins/marketplaces/my-experts/plugins/cloudops-team-pro/`.

The plugin is a **team-type** plugin: one lead agent (`cloudops-team-pro-team-lead`, "云掌柜") routes user intent to seven specialist agents (billing / huawei-billing / audit / inspection / cost-optimization / resource-inventory / architecture). Each specialist is backed by a **skill** (a directory containing a `SKILL.md` workflow + Python collector/renderer scripts + reference docs).

The repo also contains user-facing guides (`GUIDE_*.md`, `SUMMARY.md`) and a separate `codebuddy_cli_wrapper.py` for programmatic CLI invocation — these are documentation/tooling, not part of the shipped plugin.

> Note: this directory is **not a git repository** (no `.git`). There is no build step, test suite, or linter configured. Development is edit-and-run against live cloud APIs.

## Repository layout

```
cloud ops/
├── cloudops-team-pro/            # ← the actual plugin package (what gets installed)
│   ├── .codebuddy-plugin/plugin.json   # plugin manifest (agents list, skills list, member roster, tags)
│   ├── settings.json                    # {"agent": "cloudops-team-pro-team-lead"} — default agent
│   ├── agents/                          # 8 agent definition markdown files (lead + 7 specialists)
│   ├── skills/                          # 9 skills (1 shared account-picker + 7 specialist + 1 PPT)
│   │   ├── cloudops-accounts-pro/       # SHARED: SSM credential resolver + account picker
│   │   ├── cloud-billing-pro/           # Tencent Cloud billing (老账)
│   │   ├── cloud-billing-huawei-pro/    # Huawei Cloud billing (华账)
│   │   ├── cloud-auditor-pro/           # CloudAudit forensics (云审计官)
│   │   ├── cloud-inspector-pro/         # 12-domain security inspection (云巡检)
│   │   ├── cloud-costopt-pro/           # cost optimization (云降本医生)
│   │   ├── cloud-resource-pro/          # resource inventory + architecture (云管家 + 云图, shared scripts)
│   │   └── cloud-report-pptx-pro/       # PPT export (shared by all dashboard-producing skills)
│   ├── avatars/                         # PNG avatars per member
│   └── README.md
├── codebuddy_cli_wrapper.py      # Python wrapper for invoking CodeBuddy CLI programmatically
├── quickstart_examples.py        # examples for the CLI wrapper
├── GUIDE_环境搭建.md / GUIDE_专家应用指南.md / SUMMARY.md / README_codebuddy_cli.md  # user docs
├── cache/                        # runtime API-response cache (per-UIN/per-month JSON)
└── reports/                      # runtime HTML/PPT output
```

### Inside a skill directory

Every skill follows the same shape:

```
<skill>/
├── SKILL.md          # workflow SOP + hard constraints ("红线") + path conventions
├── scripts/          # Python scripts (stdlib only — see "Zero dependencies" below)
│   ├── prepare_*_data.py   # collect from cloud APIs + cache to <WORKSPACE>/cache/
│   ├── extract_*_data.py   # (billing only) distill prep output into render-ready JSON
│   ├── render_*_html.py    # render single-file HTML dashboard
│   ├── sign.py             # TC3-HMAC-SHA256 signer + call_api() (copied per-skill, NOT shared)
│   └── lib/ or query/      # sub-modules
└── references/       # api-constraints, html-template, contracts, etc.
```

## Running scripts (the "commands")

There is no build/test/lint. You run Python scripts directly with `python3`. Every collector takes `--cloud` and `--project` (resolved in-process from SSM — never exported to the shell) plus `--lang zh|en`.

```bash
# List configured cloud accounts (SSM names only, no decryption). Start here to see what's available.
python3 cloudops-team-pro/skills/cloudops-accounts-pro/scripts/list_accounts.py --human
python3 cloudops-team-pro/skills/cloudops-accounts-pro/scripts/list_accounts.py --json

# Resolve credentials for a (cloud, project) pair — prints SDK env as shell exports (debug aid only).
# In real flows specialists import resolve() in-process; this CLI is for inspection.
python3 cloudops-team-pro/skills/cloudops-accounts-pro/scripts/build_credential_env.py --cloud tencent --project YX --format json

# Example: run the billing data collector for one month (auto-fetches missing API data)
python3 cloudops-team-pro/skills/cloud-billing-pro/scripts/prepare_billing_data.py \
  --cloud tencent --project TSP --month 202605 --auto-fetch --lang zh \
  > cache/billing/TSP/prep_output.json

# Example: render the billing dashboard from cached/extracted data
python3 cloudops-team-pro/skills/cloud-billing-pro/scripts/render_billing_html.py \
  --data cache/billing/<UIN>/202605/step3_data.json \
  --output reports/tencent/TSP/<UIN>/2026-06-30/billing-tencent-TSP-<UIN>.html --lang zh

# Example: run the resource inventory collector (21 resource types, multi-region concurrent)
python3 cloudops-team-pro/skills/cloud-resource-pro/scripts/query_resources.py \
  --cloud tencent --project TSP --lang zh --out-dir cache/resource/TSP
```

**Prerequisite for any script that touches cloud APIs**: the host must have the runner key exported:
```bash
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="AKID..."   # SSM read-only, NO kms:* perms
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="..."
# optional: export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"
```
And the workspace must be pinned (or reports land inside the plugin dir and become invisible):
```bash
export WORKBUDDY_WORKSPACE="/Users/lenon/Documents/code buddy/cloud ops"
```

## Architecture: the credential model (most important concept)

All cloud credentials flow through a **single, in-process resolver** — this is the architectural keystone and the thing most likely to be broken by a careless change.

```
HOST ENV (only the runner key lives here)
  TENCENTCLOUD_BOOTSTRAP_SECRET_ID / _SECRET_KEY   [perms: ssm:GetSecretValue, ssm:ListSecrets]
        │
        ▼  ssm.GetSecretValue (SSM decrypts server-side via KMS service role — runner needs NO kms:* perm)
SSM SECRET (KMS-encrypted at rest)
        │
        ▼  build_credential_env.resolve(cloud, project)  →  dict (process-local, NEVER os.environ)
SPECIALIST PROCESS holds the dict in local vars, passes as env= to subprocess if needed
```

- **`cloudops-accounts-pro/scripts/build_credential_env.py::resolve(cloud, project)`** is the *only* entry point. It returns a dict like `{"TENCENTCLOUD_SECRET_ID": ..., "TENCENTCLOUD_SECRET_KEY": ..., "CLOUDOPS_ACTIVE_CLOUD": "tencent", ...}`.
- **Concurrency-safe by design**: under multi-account fan-out, each specialist process calls `resolve()` independently and holds creds in process-local vars. Business creds are **never** written to the shared `os.environ` (that was a real bug: last-writer-wins → account bleed across concurrent runs).
- **Cross-account STS AssumeRole** is folded into `resolve()` itself: if a secret carries a `role_arn`, `resolve()` performs `sts.AssumeRole` in-process and returns *temporary* creds + token, then strips `role_arn` so downstream can't double-assume. The caller AK comes from a separate parent secret referenced by `assume_from`.
- **Exit codes** from resolve: `0` ok · `3` bad args / reserved `BOOTSTRAP` code · `4` secret not found / missing field · `5` SSM unavailable (runner key missing) · `6` AssumeRole failed.

### ⚠️ SSM secret naming — docs vs. code disagree

The documentation (SKILL.md, README, plugin.json, agent docs) uniformly describes secrets as `cloudops/{cloud}/{project}` (slash-delimited, e.g. `cloudops/tencent/YX`).

The **actual implementation** in `cloudops-accounts-pro/scripts/lib/ssm_backend.py` uses a **hyphen-delimited form with a `-credential` suffix**:
```python
SECRET_NAME_TMPL = "cloudops-{cloud}-{project}-credential"   # e.g. cloudops-tencent-yx-credential
```
and `parse_secret_name()` splits on the same hyphen form. **Trust the code, not the docs**, when creating or matching secrets. (The `build_credential_env.py` and `list_accounts.py` modules consume `ssm_backend`, so they're consistent with the hyphen form.)

## Architecture: team-lead routing

The lead agent (`agents/cloudops-team-pro-team-lead.md`) is the only user-facing entry point. Its responsibilities, in order:

1. **Output-language arbitration** — mirror the user's prompt language (`zh`/`en`) across chat, dashboards, and PPT.
2. **Account selection** — three-layer short-circuit: (L1) keyword direct-dispatch from the prompt → (L2) uniqueness auto-skip based on the `tree` from `list_accounts.py` → (L3) adaptive `AskUserQuestion` picker (cloud step only if >1 cloud; project step only if >1 project under the chosen cloud; multi-select capped at 3 for parallel fan-out).
3. **Intent routing** — keyword → specialist mapping (see the routing table in the lead agent doc).
4. **Spawn specialists** via the Agent tool, injecting `[Account context] cloud=X project=Y` + `[Output language]` + `[Workspace]` + `[Credential handling]`. The lead **never** emits `eval`/`export` of business creds.
5. **Assemble** — rewrite each specialist's raw overview into a ≤600-word AIOps summary in `output_lang`, open the HTML via `present_files`, append a PPT-export prompt.

Specialist agent IDs and their backing skills:
| Agent | Skill | Cloud coverage |
|---|---|---|
| `cloud-billing-analyst-pro` (老账) | `cloud-billing-pro` | Tencent only |
| `huawei-billing-analyst-pro` (华账) | `cloud-billing-huawei-pro` | Huawei only |
| `cloud-auditor-pro` | `cloud-auditor-pro` | Tencent |
| `cloud-inspector-pro` | `cloud-inspector-pro` | Tencent |
| `cloud-cost-optimizer-pro` | `cloud-costopt-pro` | Tencent |
| `cloud-resource-manager-pro` (云管家) | `cloud-resource-pro` (inventory renderers) | Tencent |
| `cloud-architect-pro` (云图) | `cloud-resource-pro` (topology + assessment renderers) | Tencent |

Note `cloud-resource-pro` is **shared** by two different specialists: `render_resource_html.py` (inventory dashboard, used by 云管家) vs `render_arch_diagram.py` (topology + architecture assessment, used by 云图). The SKILL.md enforces that one specialist never produces both artifacts.

## Architecture: the per-skill render pipeline

Most skills follow a collect → (extract) → render pipeline producing a **single-file HTML dashboard** (dark glass-morphism, Chart.js via CDN, all data inlined):

1. **`prepare_*_data.py`** — resolves creds via `resolve(cloud, project)`, calls cloud APIs (concurrent where possible), writes raw JSON to `<WORKSPACE>/cache/<type>/<UIN>/<YYYYMM>/`, and emits a prep-output JSON containing a `report_ready` field. **The presence of `report_ready.available == True` is the hard gate** before rendering — the billing SKILL.md explicitly forbids entering the render step otherwise.
2. **`extract_*_data.py`** (billing only) — distills the large prep output into a render-ready `step3_data.json` (<100KB) so the LLM doesn't read huge files into context.
3. **`render_*_html.py`** — pure local render from the JSON; takes `--lang`, `--output`, `--data`. No cloud API access, no credentials.

### Caching discipline

- Cache key = UIN + month, stored under `<WORKSPACE>/cache/<skill>/<UIN>/<YYYYMM>/`.
- Historical months are stable → cache-hit fast path (billing targets ≤60s LLM time on cache hit).
- **Skip cache** (must re-fetch) for: the current month, and T-1 month during days 1–4 of the month (books still settling).
- "重新分析 / refresh / 不走缓存" in the user prompt → `rm -rf` the month's cache dir first.
- **Never** read a >500KB JSON file directly into LLM context — summarize via `summarize_bill.py` first (the SKILL.md calls this out as a HARD efficiency constraint).

## Output paths and naming convention

All HTML/PPT reports land under `<WORKSPACE>/reports/{cloud}/{project}/{uin}/{YYYY-MM-DD}/` with a stable filename pattern `{type}-{cloud}-{project}-{uin}.html`:

```
reports/tencent/TSP/100031104408/2026-06-30/billing-tencent-TSP-100031104408.html
reports/tencent/TSP/100031104408/2026-06-30/resource-tencent-TSP-100031104408.html
reports/tencent/TSP/100031104408/2026-06-30/arch-tencent-TSP-100031104408.html
```

- `{project}` = the **project code** (e.g. `TSP`, `YX`), **not** the account nickname (nicknames drift / can be empty / can contain Chinese). This is a hard naming rule across all skills.
- `{uin}` = auto-derived from `cam.GetUserAppId` (Tencent) / equivalent (Huawei/Aliyun) — **never** ask the user for UIN.
- `{YYYY-MM-DD}` = the **landing date** (today), not the analyzed month.

## Key conventions and gotchas

- **Zero third-party dependencies.** Every script uses only the Python stdlib (verified: `argparse`, `json`, `hashlib`, `hmac`, `urllib`, `concurrent`, `threading`, etc.). Cloud API calls use a hand-rolled **TC3-HMAC-SHA256** signer copied into each skill as `sign.py` (Tencent) — there is no `tencentcloud-sdk-python` dependency. Do not introduce `pip install` requirements; the scripts must run on macOS managed Python (3.9+).
- **`sign.py` is duplicated per-skill**, not shared. Each skill has its own copy (`skills/<skill>/scripts/sign.py` or `scripts/lib/sign.py`). The SSM backend has its own copy too. When fixing a signing bug, check whether the same fix is needed in sibling copies.
- **Bilingual output is mandatory and easy to break.** `--lang zh|en` is threaded through every renderer; the lead injects `[Output language] output_lang=zh|en` into every spawn. The most common bug: an English prompt produces a Chinese overview because the runtime default is Chinese. Enumerations (product names, region names) must also be translated when `output_lang=en`.
- **`WORKBUDDY_WORKSPACE` must be exported** before running any renderer, or `--output` relative paths resolve inside the plugin directory and the artifact panel won't show them.
- **Credentials are read-only and never solicited.** Never ask the user to paste AK/SK in chat, never write `.env` files, never `eval`/`export` business creds. The only host-side credential is the runner key. `BOOTSTRAP` is a reserved project code that can never be selected as a business account.
- **SKILL.md files contain hard constraints ("红线")** that override exploration instincts — e.g. billing forbids writing custom `fetch_*.py` scripts (they lack a `report_ready` field and trigger explore-loops), forbids reading >500KB JSON into context, and mandates `prepare_billing_data.py` as the only data-prep path. Read the relevant SKILL.md's "全局红线" and "执行效率" sections before modifying a skill's flow.
- **Region auto-discovery in `ssm_backend.py`**: SSM is region-scoped, so `_discover_region()` probes a candidate list (`ap-shanghai` first) and caches the first region that holds `cloudops-*` secrets for the process lifetime. `TENCENTCLOUD_BOOTSTRAP_REGION` is **not** read by the backend (intentional design decision) — only `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/_KEY`.
- **Dashboard-producing skills offer PPT export** via the shared `cloud-report-pptx-pro` skill (`build_pptx.py`), triggered only on explicit user request ("导出 PPT"). It reuses the already-generated overview + render data; it does not re-collect.

## Installing / testing the plugin locally

```bash
# 1. Register the local marketplace source (one-time)
codebuddy plugin marketplace add /Users/lenon/.workbuddy/plugins/marketplaces/my-experts
# (the cloudops-team-pro dir should be symlinked/copied under .../my-experts/plugins/cloudops-team-pro)

# 2. Install
codebuddy plugin install cloudops-team-pro@my-experts

# 3. Verify
codebuddy plugin list     # expect: cloudops-team-pro v2.0.0, 8 agents, 9 skills
```

User-facing setup (runner key + SSM secrets) is documented in `GUIDE_环境搭建.md`; usage patterns are in `GUIDE_专家应用指南.md`.
