---
name: cloudops-accounts-pro
description: Multi-cloud / multi-UIN account picker. Lists cloud accounts whose KMS-encrypted credentials are stored in Tencent Cloud SSM (Secrets Manager) under the `cloudops/{cloud}/{project}` naming convention, and assembles SDK-standard credentials for a given (cloud, project) pair by decrypting the secret at use time. Used by the cloudops-team lead and all specialists to support multi-account workflows without ever placing business credentials in the host environment.
---

# cloudops-accounts-pro — multi-cloud account picker (SSM + KMS only)

This skill is the **single source of truth** for "which cloud accounts are configured for this host" and "how to turn `(cloud, project)` into the SDK-standard env vars expected by the underlying scripts (sign.py / Tencent SDK / Huawei SDK)".

> **Design contract (SSM-only, MANDATORY)**: business credentials live **exclusively** in Tencent Cloud SSM, KMS-encrypted. They are NEVER set as host environment variables, NEVER written to `.env` / `~/.zshrc`, and NEVER printed to a shell. The ONLY credential allowed in the host environment is the **runner (bootstrap) key**, whose sole job is to call SSM `GetSecretValue` / `ListSecrets` (it cannot sign any business API). Callers (the team lead, the 5 specialists) only deal with the abstract `(cloud, project)` tuple; downstream scripts still receive `TENCENTCLOUD_SECRET_ID` / `HUAWEICLOUD_SDK_AK` as a **process-local dict** at use time. No env-prefix scheme, no bare-name fallback, no `eval`/`export` of business credentials.

---

## 0. Trust model (read first)

```
┌─────────────────────────────────────────────────────────────┐
│ HOST ENV  (the ONLY credential here is the runner key)        │
│   TENCENTCLOUD_BOOTSTRAP_SECRET_ID                            │
│   TENCENTCLOUD_BOOTSTRAP_SECRET_KEY  ← perms: ssm:GetSecretValue│
│   TENCENTCLOUD_BOOTSTRAP_REGION              + ssm:ListSecrets │
│   (CLOUDOPS_SSM_ENABLED optional)    (NO kms perm; cannot sign)│
└───────────────┬─────────────────────────────────────────────┘
                │ ssm.GetSecretValue("cloudops/tencent/YX")
                ▼  (SSM service-role decrypts via KMS, server-side)
┌─────────────────────────────────────────────────────────────┐
│ SSM SECRET  cloudops/tencent/YX   (KMS-encrypted at rest)     │
│   SecretString = {                                            │
│     "secret_id":  "AKIDxxxx",                                 │
│     "secret_key": "yyyy",                                     │
│     "region":     "ap-guangzhou",     (optional)              │
│     "uin":        "100012345678"      (optional, display)     │
│   }                                                           │
└───────────────┬─────────────────────────────────────────────┘
                │ build_credential_env.resolve("tencent","YX")
                ▼  (decrypted payload → process-local dict)
┌─────────────────────────────────────────────────────────────┐
│ SPECIALIST PROCESS  (in-memory dict, never os.environ)        │
│   {"TENCENTCLOUD_SECRET_ID": "...", "TENCENTCLOUD_SECRET_KEY":│
│    "...", "TENCENTCLOUD_REGION": "...", "INSPECTION_UIN":...} │
└─────────────────────────────────────────────────────────────┘
```

Three reasons this is the only allowed model:
1. **No plaintext business credential ever touches the host** — env, disk, or shell history.
2. **Runner key is minimally scoped** — even if leaked, it can only read secrets through SSM/KMS, not sign CVM/COS/billing APIs directly.
3. **Concurrency-safe under fan-out** — each specialist decrypts its own `(cloud, project)` into a process-local dict; concurrent runs cannot clobber a shared `os.environ`.

---

## 1. SSM secret naming convention (authoritative)

```
cloudops/{cloud}/{project}
```

| Cloud | Secret name example | SecretString JSON keys (lower-case) |
|---|---|---|
| Tencent Cloud | `cloudops/tencent/YX` | required `secret_id`, `secret_key`; optional `region`, `uin`; **cross-account (optional)** `role_arn`, `assume_from`, `role_external_id`, `role_duration` |
| Huawei Cloud | `cloudops/huawei/YX` | required `ak`, `sk`; optional `bss_endpoint`, `project_id`, `domain_id` |
| Alibaba Cloud | `cloudops/aliyun/YX` | required `access_key_id`, `access_key_secret`; optional `region_id` |

- `{cloud}` is lower-case (`tencent` / `huawei` / `aliyun`).
- `{project}` is the project code: 2-10 chars, uppercase `A-Z` and digits, no underscores.
- **SecretString is a single JSON object** with lower-case keys (see §2.3 mapping). Store it KMS-encrypted (bind a CMK when creating the secret). **Decryption is done by SSM server-side via its KMS service role** (`SSM_QCSRole`, authorized once when you activate SSM) — the runner key itself does NOT need any `kms:*` permission; `ssm:GetSecretValue` returns the already-decrypted plaintext over TLS.
- **Reserved project code `BOOTSTRAP`**: never use it as a business project. It is reserved to denote the runner key itself and is filtered out by `list_accounts.py` / rejected by `build_credential_env.resolve()` (exit 3). This prevents the runner key from showing up as a selectable ghost account.

> **UIN / REGION / DOMAIN_ID auto-detection**: the `uin` / `region` fields inside the secret are **optional display pre-fills** only. At specialist runtime the canonical source is still `cam.GetUserAppId` (Tencent) / IAM `listAuthDomains` (Huawei) / `sts.GetCallerIdentity` (Aliyun) — every `prepare_*_data.py` auto-detects these. Omitting them costs nothing functionally; including them just makes the picker preview prettier. Note: since `list_accounts.py` lists names WITHOUT decrypting, UIN preview is only available after the secret is actually used.

### Storing a secret (admin task, one-time per account)

The admin (someone holding a key with SSM `CreateSecret` + `PutSecretValue` + KMS `Encrypt`) stores the business credential. Example via Tencent Cloud SSM console or `tccli`:

```bash
# 1. Create the secret bound to a KMS CMK
tccli ssm CreateSecret \
  --SecretName cloudops/tencent/YX \
  --VersionId v1 \
  --KmsKeyId <your-cmk-id> \
  --SecretString '{"secret_id":"AKIDxxxxxxxx","secret_key":"yyyyyyyy","region":"ap-guangzhou"}'

# Huawei example
tccli ssm CreateSecret \
  --SecretName cloudops/huawei/YX \
  --VersionId v1 \
  --KmsKeyId <your-cmk-id> \
  --SecretString '{"ak":"HW_AK_xxxx","sk":"HW_SK_yyyy","bss_endpoint":"bss.myhuaweicloud.com"}'
```

### Cross-account proxy query (STS AssumeRole, optional — Tencent)

`build_credential_env.resolve()` is the **single** cross-account entry point. If a Tencent
secret carries a `role_arn`, `resolve()` performs `sts.AssumeRole` IN-PROCESS and returns the
*temporary* `TmpSecretId` / `TmpSecretKey` / `Token` instead of the caller AK — and strips
`role_arn` from its result so no downstream re-assumes. Specialists need ZERO assume code.

Two ways to provide the caller (main-account) AK that signs AssumeRole:

```bash
# (Recommended) assume_from: main-account AK stored ONCE in a parent secret, target
# secret only references it. Rotating the main AK = edit one secret.
tccli ssm CreateSecret --SecretName cloudops/tencent/PARENT --VersionId v1 --KmsKeyId <cmk> \
  --SecretString '{"secret_id":"AKID-main","secret_key":"..."}'
tccli ssm CreateSecret --SecretName cloudops/tencent/ADC --VersionId v1 --KmsKeyId <cmk> \
  --SecretString '{"role_arn":"qcs::cam::uin/100099998888:roleName/CloudOpsReader","assume_from":"PARENT"}'

# (Alternative) inline: main-account AK lives in the SAME secret as role_arn. Simpler,
# but the main AK is duplicated into every target secret (rotation touches N secrets).
tccli ssm CreateSecret --SecretName cloudops/tencent/TSP --VersionId v1 --KmsKeyId <cmk> \
  --SecretString '{"secret_id":"AKID-main","secret_key":"...","role_arn":"qcs::cam::uin/100077776666:roleName/Reader"}'
```

Optional inside the secret: `role_external_id` (if the target role's trust policy requires
an ExternalId) and `role_duration` (temp-cred TTL in seconds, default 3600). On AssumeRole
failure `resolve()` exits 6. Direct-connect (no `role_arn`) is unchanged.

> ⚠️ Console "SwitchRole" (click-link + WeChat confirm) ≠ STS AssumeRole (API). To let the
> skill query another account, you MUST create a role in the target account that trusts the
> caller's main account, then put its ARN into the target secret's `role_arn`.

> The skill itself NEVER creates or writes secrets — storing credentials is an explicit admin action outside this skill. The skill only **reads** (lists names + decrypts at use time).

### Runner (bootstrap) key — the only host env config

```bash
# ~/.workbuddy/secrets/cloudops.env  — the ONLY credential on the host
export TENCENTCLOUD_BOOTSTRAP_SECRET_ID="AKID<runner-key>"
export TENCENTCLOUD_BOOTSTRAP_SECRET_KEY="<runner-key-secret>"
export TENCENTCLOUD_BOOTSTRAP_REGION="ap-guangzhou"   # optional, default ap-guangzhou
# CLOUDOPS_SSM_ENABLED is OPTIONAL — having the runner key already enables SSM.
# Only set it to false/0/no/off as an emergency kill-switch (disable SSM without deleting the key).
```

Runner-key CAM policy (minimal — read secrets only, NO KMS perm, NO business actions):

```json
{
  "version": "2.0",
  "statement": [
    {"effect": "allow",
     "action": ["ssm:GetSecretValue", "ssm:ListSecrets", "ssm:ListSecretVersionIds"],
     "resource": "qcs::ssm:*:uin/*:secret/cloudops/*"}
  ]
}
```

> **Why no `kms:Decrypt`?** Tencent Cloud SSM decrypts the secret with KMS **on the service side**, under SSM's own service role (`SSM_QCSRole`), which is authorized to use KMS the moment you activate SSM and click "Grant" on the service-authorization page. `ssm:GetSecretValue` then hands back the plaintext over TLS. So the caller (runner key) never invokes KMS directly and must NOT be granted `kms:Decrypt` — that would only widen the blast radius for zero benefit. (This differs from AWS, where the caller's own identity needs `kms:Decrypt`.)

---

## 2. Public scripts

### 2.1 `scripts/list_accounts.py`

Lists secret **names** from SSM (no decryption) and prints the account inventory as JSON.

```bash
python3 scripts/list_accounts.py            # JSON to stdout
python3 scripts/list_accounts.py --human    # human-readable table
```

- Requires a valid runner key (and SSM not explicitly disabled), else exits **5** (SSM not available) with guidance.
- Calls `ssm.ListSecrets`, keeps only names matching `cloudops/{cloud}/{project}`, skips `BOOTSTRAP`. **Never decrypts** — so the inventory carries only `cloud` / `project` / `secret_name`. UIN/region live inside the KMS-encrypted payload and are revealed only at use time.

**JSON output shape**:
```json
{
  "source": "ssm",
  "accounts": [
    {"cloud": "tencent", "project": "YX",  "secret_name": "cloudops/tencent/YX"},
    {"cloud": "tencent", "project": "TSP", "secret_name": "cloudops/tencent/TSP"},
    {"cloud": "huawei",  "project": "YX",  "secret_name": "cloudops/huawei/YX"}
  ],
  "tree": {
    "tencent": ["YX", "TSP"],
    "huawei":  ["YX"]
  },
  "total": 3
}
```

The team lead reads `tree` to render the two-step picker (cloud → project) via `AskUserQuestion`.

### 2.2 `scripts/build_credential_env.py`

Decrypts the SSM secret for `(cloud, project)` and maps its JSON fields to SDK-standard env names. **Single interface — in-process `resolve()` only.** There is no `eval`/`export` interface: business credentials must never reach a shell.

**`resolve(cloud, project) -> dict` — in-process, concurrency-safe (the only supported entry).**

```python
import build_credential_env as bce          # scripts dir is on sys.path
env = bce.resolve("tencent", "YX")          # raises SystemExit on missing/invalid
sid = env["TENCENTCLOUD_SECRET_ID"]          # stays local to this process
sk  = env["TENCENTCLOUD_SECRET_KEY"]
# env also carries CLOUDOPS_ACTIVE_CLOUD / CLOUDOPS_ACTIVE_PROJECT for report-header bucketing
```

Each specialist's collector accepts `--cloud X --project Y` and calls `resolve(X, Y)` internally, holding the returned dict in process-local variables — never writing back to `os.environ`. When credentials must cross a process boundary, pass the dict as `env=` to `subprocess.run` (a private copy), never mutate the parent environment.

**Exit codes**:

| Code | Meaning |
|---|---|
| 0 | Success |
| 3 | Invalid args, or project == reserved `BOOTSTRAP` |
| 4 | Secret not found in SSM, or a required JSON field is missing |
| 5 | SSM not available (runner key missing, or SSM explicitly disabled via `CLOUDOPS_SSM_ENABLED=false`) |

`--project` is **mandatory** — there is no bare-name fallback. A missing project is an error, not a single-account shortcut.

### 2.3 Mapping table (cloud × SSM JSON key → SDK env var)

| Cloud | SSM JSON key (input, lower-case) | SDK env (output) | Required |
|---|---|---|---|
| tencent | `secret_id` | `TENCENTCLOUD_SECRET_ID` | yes |
| tencent | `secret_key` | `TENCENTCLOUD_SECRET_KEY` | yes |
| tencent | `region` | `TENCENTCLOUD_REGION` | no |
| tencent | `uin` | `INSPECTION_UIN` (also pinned for report header) | no |
| huawei  | `ak` | `HUAWEICLOUD_SDK_AK` | yes |
| huawei  | `sk` | `HUAWEICLOUD_SDK_SK` | yes |
| huawei  | `project_id` | `HUAWEICLOUD_SDK_PROJECT_ID` | no |
| huawei  | `domain_id` | `HUAWEICLOUD_SDK_DOMAIN_ID` | no |
| huawei  | `bss_endpoint` | `HUAWEICLOUD_BSS_ENDPOINT` | no |
| aliyun  | `access_key_id` | `ALIBABA_CLOUD_ACCESS_KEY_ID` | yes |
| aliyun  | `access_key_secret` | `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | yes |
| aliyun  | `region_id` | `ALIBABA_CLOUD_REGION_ID` | no |

`resolve()` also always pins `CLOUDOPS_ACTIVE_CLOUD` / `CLOUDOPS_ACTIVE_PROJECT` so report templates can label the active account in the header.

### 2.4 `scripts/lib/ssm_backend.py`

The SSM access layer. Hand-rolled TC3-HMAC-SHA256 signing, **zero third-party deps** (stdlib only), runs on managed Python. Public helpers:

- `fetch_secret(cloud, project) -> dict | None` — `ssm.GetSecretValue("cloudops/{cloud}/{project}")`, parses SecretString JSON, normalizes keys to lower-case. `None` if not found.
- `list_secret_names() -> list[str]` — `ssm.ListSecrets`, returns `cloudops/...` names only.
- `parse_secret_name(name) -> (cloud, project) | None` — validates against `cloudops/{cloud}/{project}`.
- `is_ssm_available() -> bool` — runner key present AND not explicitly disabled (`CLOUDOPS_SSM_ENABLED` ∉ {false,0,no,off}).

---

## 3. Lead-side workflow (team-lead reference)

The team lead `cloudops-team-pro-team-lead` orchestrates account selection through three layers — see the lead's agent doc for the full algorithm. This skill only provides the data + assembly primitives.

| Layer | Trigger | Tool |
|---|---|---|
| 1. Keyword direct dispatch | User prompt contains `(cloud, project)` keywords | Lead parses prompt, no UI |
| 2. Auto-skip (dynamic) | Decision matrix below — based on `tree` shape from `list_accounts.py` | No UI for any "auto" branch |
| 3. Adaptive picker | Only the dimensions still ambiguous get rendered | `AskUserQuestion` × 0/1/2 |

**Layer 2 dynamic skip matrix** (the lead reads `tree` from `list_accounts.py --json`):

| `tree` shape | Step 1 (cloud) | Step 2 (project) |
|---|---|---|
| `{}` no accounts | render SSM setup guide (store secret + runner key) | — |
| `{tencent:[YX]}` single cloud + single project | skip | skip → direct dispatch |
| `{tencent:[YX,TSP]}` single cloud + multi project | skip (cloud unique) | render multi-select |
| `{tencent:[YX], huawei:[YX]}` multi cloud + each single | render | skip on chosen cloud |
| `{tencent:[YX,TSP], huawei:[YX]}` mixed | render | render conditionally |

**Step 2 multi-select rules**:
- `multiSelect: true`, **hard cap = 3 projects per run** (specialist instances spawn in parallel; cap protects API rate limits).
- **No `all` option** — ticking all visible items already expresses "pick everything ≤3"; an explicit `all` conflicts with the 3-instance cap.
- Each selected project triggers an independent specialist spawn with its own `(cloud, project)`; the specialist calls `build_credential_env.resolve()` in-process.
- To target a project beyond the visible 3, fall back to Layer 1 keyword direct-dispatch (e.g. "analyze tencent gw last month bill").

**Add-new entry points (always present)**:
- Step 1 always carries a final option `+ add new cloud` (description: "Store a new cloud account in SSM").
- Step 2 always carries a final option `+ add new project` (description: "Store another project under <cloud> in SSM").
- AskUserQuestion has a hard cap of 4 options. When real options + `+ add new` exceeds 4, keep `+ add new` as the 4th slot, fill the first 3 with projects in alphabetical order, and note "4+ projects detected, showing first 3. Use a prompt keyword to pick others."
- Selecting `+ add new ...` switches the lead into **config-guide mode**: print the SSM secret-storage guidance (admin stores `cloudops/{cloud}/{project}` KMS-encrypted, ensure the runner key is set), then **stop** — do not spawn any specialist; tell the user to re-run after the secret is stored.
- Never try to create/modify SSM secrets automatically — storing credentials is an explicit admin action.

Once `(cloud, project)` is decided, the lead **passes the pair to the specialist** via the spawn prompt, e.g.:

```
You are dispatched on this task:
  cloud=tencent  project=TSP
Pass `--cloud tencent --project TSP` to your collector scripts; they call
build_credential_env.resolve("tencent","TSP") in-process, which decrypts the
SSM secret cloudops/tencent/TSP — NO eval, NO export to the shell, NO env
prefix. Then proceed with the user's request: "<original user prompt>".
```

Every specialist agent doc has this in-process pattern in its workflow §0 "Credential handling". **The lead never emits `eval`/`export`** and never asks the user to set business env vars — credentials only ever come from SSM, resolved in-process by each collector.

---

## 4. Safety red lines

1. **SSM is the only credential source.** No env-prefix scheme, no bare-name fallback, no `.env` business credentials. The only host credential is the runner key (`TENCENTCLOUD_BOOTSTRAP_*`), and it cannot sign business APIs.
2. **Never print SK/SecretKey to stdout.** `list_accounts --human` shows names only (it never decrypts). `resolve()` returns a dict consumed in-process; never echo it to a shell.
3. **Never write credentials to disk.** This skill is read-only against SSM; it never writes `.env`, `~/.zshrc`, or any persistent store. Secrets are created by the admin out-of-band.
4. **Project code validation**: must match `^[A-Z][A-Z0-9]{1,9}$`; the reserved code `BOOTSTRAP` is rejected (exit 3). Reject anything else with an explicit error.
5. **Missing secret / field → fail loud.** If `cloudops/tencent/YX` is absent, or its JSON lacks `secret_key`, exit 4 with a structured message. Never fall back silently.
6. **Process-local credentials.** Hold the `resolve()` dict in local vars; cross process boundaries only via `env=` on `subprocess.run` (private copy). Never mutate the parent `os.environ` with business credentials.

---

## 5. Common errors & remediation

| Symptom | Root cause | Fix |
|---|---|---|
| `list_accounts` / `resolve` exits 5 | Runner key missing, or SSM explicitly disabled | Set `TENCENTCLOUD_BOOTSTRAP_SECRET_ID/_KEY` (see §1); ensure `CLOUDOPS_SSM_ENABLED` is not set to false/0/no/off |
| `list_accounts` returns empty `tree` | No `cloudops/{cloud}/{project}` secrets stored, or runner key lacks `ssm:ListSecrets` | Admin stores the secret; verify runner-key CAM policy |
| `resolve` exits 4 "secret not found" | Secret name typo, wrong region, or not yet stored | Confirm `cloudops/{cloud}/{project}` exists in the runner-key's region |
| `resolve` exits 4 "missing field secret_key" | SecretString JSON incomplete | Admin re-stores the secret with all required keys |
| `resolve` exits 3 "reserved code" | Tried to use `BOOTSTRAP` as a business project | Use a real project code; `BOOTSTRAP` is the runner key only |
| `GetSecretValue` returns `FailedOperation.AccessKmsError` | The SSM **service role** isn't authorized to use KMS (you skipped the one-time "Grant" when activating SSM), or the bound CMK is disabled | In the SSM console click the CAM/KMS authorization banner → Grant; ensure the CMK is enabled. **Do NOT** add `kms:Decrypt` to the runner key — that's not where the perm belongs |

---

## 6. Migration note (from the legacy env-prefix model)

Earlier versions of this skill scanned env vars named `{CLOUD}_{PROJECT}_{FIELD}` and supported a bare-name `DEFAULT` fallback. **That model is fully removed.** To migrate:

1. For each account, move the AK/SK out of `~/.zshrc` into an SSM secret `cloudops/{cloud}/{project}` (KMS-encrypted) — see §1.
2. Delete the old `TENCENTCLOUD_{P}_SECRET_ID/_KEY` (and bare `TENCENTCLOUD_SECRET_ID/_KEY`) exports from your shell config.
3. Provision a minimally-scoped runner key and set `TENCENTCLOUD_BOOTSTRAP_*` (having the key enables SSM automatically; `CLOUDOPS_SSM_ENABLED` is an optional kill-switch only).
4. Re-run `list_accounts.py` to confirm the `tree` is populated from SSM (`"source": "ssm"`).

There is no automatic dual-read bridge: once a host is on the runner key, all business credentials must be in SSM.
