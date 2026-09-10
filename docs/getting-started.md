# Getting started, from nothing

This assumes you have no AWS account and none of the tooling installed. It is written
to be worked through in order, and it is roughly Phase 0 of the roadmap — the part
that has to happen before any of the code in this repository can run.

Budget about two hours for the whole thing, most of which is waiting.

---

## 1. What you actually need to sign up for

Only one account: **AWS**.

That is worth stating plainly, because it is a common point of confusion. Amazon
Bedrock is a service *inside* AWS that hosts Claude. You do not need a separate
Anthropic account, an Anthropic API key, or a Claude subscription — the models are
billed through your AWS bill, and the Lambda authenticates to them with its IAM role
rather than with any key you have to manage.

You will also want a **GitHub account** if you want this backend on a remote, but
nothing here depends on it.

### Creating the AWS account

1. Go to <https://aws.amazon.com/> and choose *Create an AWS Account*.
2. It requires a **credit or debit card**, even though almost everything here sits in
   the free tier. AWS places a small temporary authorisation (usually about $1) to
   verify the card, which drops off.
3. Pick the **Basic support plan** — it is free. Do not take a paid support plan.
4. Verify by phone and email.

**Set a budget alarm before you do anything else.** This is the single most useful ten
minutes you will spend, because Bedrock is billed per use and a mistake in a loop is
otherwise invisible until the bill arrives:

- Console → search *Billing and Cost Management* → **Budgets** → *Create budget*
- Choose *Zero spend budget* to get an email at the first cent, or a *Monthly cost
  budget* set to something like $10 with alerts at 50% / 80% / 100%.

### Stop using the root user

The email address you signed up with is the **root user**, and it can do anything
including closing the account. Do not create access keys for it.

- Console → **IAM** → *Users* → *Create user*
- Give it a name (`jake-dev` or similar), tick *Provide user access to the AWS
  Management Console* if you want to sign in as it
- Attach the `AdministratorAccess` policy — broader than this project strictly needs,
  but the alternative for a solo project is fighting permissions errors all semester
- After creating: *Security credentials* → *Create access key* → choose
  **Command Line Interface (CLI)** → save the access key id and secret

Turn on MFA for both the root user and this one while you are in there.

---

## 2. Software to install

| Tool | Why | Windows install |
|---|---|---|
| **Python 3.12+** | Runs the Lambda code and the tests | <https://python.org> or `winget install Python.Python.3.12` |
| **AWS CLI v2** | Everything in `scripts/` is a wrapper over it | `winget install Amazon.AWSCLI` |
| **AWS SAM CLI** | Builds and deploys the stack | `winget install Amazon.SAM-CLI` |
| **Docker Desktop** | `sam build --use-container` and `sam local invoke` | <https://docker.com/products/docker-desktop> |
| **Git** | Version control | `winget install Git.Git` |

Docker is the one you can defer. It is needed because `samconfig.toml` sets
`use_container = true`, which compiles the Python dependencies inside a Lambda-like
Linux container. That matters here specifically: the `anthropic` SDK pulls in packages
with compiled components, and a wheel built on Windows will not run on Amazon Linux.
You can set `use_container = false` and try your luck, but this is the failure mode
that produces a confusing `ImportError` at runtime rather than a build error.

Close and reopen your terminal after installing, then check:

```bash
python --version && aws --version && sam --version && docker --version
```

`docker --version` only proves the CLI is installed, not that the engine is running.
The real check is:

```bash
docker info
```

### If Docker will not start

Installing Docker Desktop is not the same as provisioning it, and the gap between the
two produces a genuinely confusing failure: `sam build` reports
`requires a container runtime`, and Docker Desktop itself says only
`Docker Desktop is unable to start`, with no log file written at all.

The diagnostic that actually tells you what is wrong:

```bash
wsl -l -v
```

You should see **two** distributions — your Linux distro *and* `docker-desktop`. If
`docker-desktop` is missing, Docker has never completed its first-run setup, which it
needs administrator rights to do. Symptoms line up: no log file, and a pile of stalled
`Docker Desktop` processes from repeated launch attempts.

The fix, in order:

1. **Clear the jam.** Repeated launches leave processes stacked up, and they block each
   other. In PowerShell:
   ```powershell
   Get-Process -Name '*docker*' -ErrorAction SilentlyContinue | Stop-Process -Force
   wsl --shutdown
   ```
2. **Launch once, elevated.** Start menu → right-click *Docker Desktop* → **Run as
   administrator**. This is the step that creates the `docker-desktop` distro.
3. **Leave it alone** for three to five minutes. It is building that distro from
   scratch. Clicking again just recreates the pile-up from step 1.
4. Confirm with `wsl -l -v` that `docker-desktop` now exists, then `docker info`.

If it still fails, Docker Desktop → ⚙ Settings → **Troubleshoot** → **Reset to factory
defaults**, which reruns provisioning from scratch.

Worth ruling out first, since both are silent failures: virtualisation must be enabled
in firmware, and WSL 2 must be present. Check both with:

```powershell
(Get-CimInstance Win32_ComputerSystem).HypervisorPresent   # must be True
wsl --version
```

### Point the CLI at your account

```bash
aws configure
```

It asks for the access key id and secret from the IAM user above, a default region
(use `us-east-1` — it has the widest Bedrock model availability), and an output format
(`json`). Confirm it worked:

```bash
aws sts get-caller-identity
```

That should print your account id and the ARN of the IAM user. If it prints an error,
nothing further in this document will work.

---

## 3. Enable the model

**AWS retired the Model access page.** Serverless foundation models are now enabled
automatically the first time your account invokes them, across all AWS commercial
regions. There is no longer a page to go and tick boxes on, and no waiting for an
approval to land.

Two things still stand between a fresh account and a working call:

- **Anthropic models may ask first-time users for use-case details.** This is what
  survives of the old approval step. It is much quicker than the old flow, but it is
  still a form that can appear.
- **Marketplace-served models** need one invocation by a user with AWS Marketplace
  permissions before they are enabled account-wide. On a solo admin account that is
  just you.

Both are best triggered deliberately, by invoking the model by hand once, rather than
discovered later as a Lambda error.

### Invoke it once in the Playground

1. Console → search **Bedrock** → check the region selector (top right) says the region
   you plan to use, e.g. *US East (N. Virginia)*
2. Left sidebar → **Model catalog** → find the Claude model you want
3. Open it in the **Playground** and send any message — `hello` is enough
4. If a use-case form appears, fill it in. A student project explanation is fine: say
   it is an educational tool that turns pre-computed calculus results into
   plain-language explanations, which is exactly what it is.
5. When you get a reply back, the model is enabled account-wide and you are done

That reply is the thing worth waiting for. It proves the exact combination that
matters — this account, this region, this model — which is the same combination the
Lambda will use.

### Then note the model id

```bash
./scripts/list-models.sh us-east-1
```

or, from PowerShell:

```powershell
. .\scripts\Spatial-Math.ps1
Get-SpatialMathModels
```

Whatever id you used in the Playground goes into `samconfig.toml` as `ModelId`.

**Check the generation, not just the name.** Bedrock's catalog still lists deprecated
models, and Claude Sonnet 4 (`claude-sonnet-4-20250514`) is one of them — it predates
adaptive thinking and the `effort` setting, so the Lambda automatically falls back to a
conservative request shape for it (you will see `model_tuning_conservative` in
CloudWatch). It works, but it is a model with a retirement date. Prefer
`claude-sonnet-4-5` if that is what your account offers, and `claude-sonnet-5` if it is
available.

Be aware of what this listing does and does not tell you now. `list-foundation-models`
reports the models **offered** in that region. Since enablement happens at first invoke
rather than up front, appearing on that list no longer proves you can call it — which
is exactly why step 3 above is a Playground message and not just a CLI listing.

A model id that your account cannot invoke fails at *request* time with a 404 or 403,
not at deploy time. So the stack deploys cleanly and then every click fails, which is a
far more confusing thing to debug than a failed deploy. Ninety seconds in the Playground
removes that whole class of problem.

Admins keep control of all this through IAM policies and Service Control Policies. With
the `AdministratorAccess` user from step 1 there is nothing to configure — but it is why
a locked-down corporate account can still refuse, and the Lambda reports that as
`model_access_denied`.

---

## 4. Deploy

```bash
sam build
sam deploy
```

The first `sam deploy` prompts you to confirm the changeset — read it, it is showing
you every resource it is about to create. Subsequent deploys reuse `samconfig.toml`.

When it finishes it prints the outputs. The one you need is `ApiEndpoint`.

Then fetch the API key:

```bash
./scripts/get-api-key.sh
```

```powershell
. .\scripts\Spatial-Math.ps1
Get-SpatialMathApiKey
```

And check the whole thing end to end:

```bash
./scripts/smoke-test.sh
```

**Run the smoke test twice.** The first response should say `"cached": false` and the
second `"cached": true`. That second run is the real test — it proves the DynamoDB
layer is working, and every cache hit is a Bedrock call you did not pay for.

---

## 5. Wire up Unity

See [`unity-client/README.md`](../unity-client/README.md). It is four files to copy and
two fields to fill in.

---

## What this costs

Everything except Bedrock is effectively free at this scale:

| Service | At this scale |
|---|---|
| Lambda | Free tier covers 1M requests/month. Nothing. |
| API Gateway (REST) | ~$3.50 per million requests. A few cents a month. |
| DynamoDB (on-demand) | Pennies. It is idle almost always, and there is no provisioned capacity billing around the clock. |
| CloudWatch Logs | Retention is capped at 14 days in the template so it cannot creep. |
| **Bedrock** | **The only part that meaningfully costs money.** |

A single click sends roughly 1,000 input tokens (the system prompt dominates) and gets
back a few hundred output tokens.

Anthropic's first-party list prices are $5 / $25 per million input / output tokens for
Opus 5, $2 / $10 for Sonnet 5, and $1 / $5 for Haiku 4.5. On those numbers a click is
somewhere around **1–2 cents on Opus, well under a cent on Sonnet or Haiku**.

Two important caveats on those figures:

- **Bedrock is billed by AWS at its own rates**, which are not necessarily the
  first-party ones. Check <https://aws.amazon.com/bedrock/pricing/> for the real
  number before you rely on any of this.
- The template ships with `MonthlyQuota=2000`. At Opus rates, 2,000 uncached clicks is
  on the order of **$20–30 a month** if you actually used the whole quota. You almost
  certainly will not — the cache absorbs repeat clicks and a demo is a few dozen calls
  — but that is the ceiling you have authorised, and it is worth knowing rather than
  discovering.

If that ceiling makes you uncomfortable, you have three levers, in order of how much
they cost you in quality:

1. **Lower the quota.** `MonthlyQuota=300` in `samconfig.toml` caps the worst case at a
   few dollars and is still far more than a demo needs. Costs you nothing.
2. **Leave `Effort=low`.** Already the default. This endpoint restates numbers it was
   handed, which is a genuinely simple task, so low effort is the right setting on the
   merits rather than a compromise.
3. **Switch models.** `ModelId=anthropic.claude-sonnet-5` cuts the per-call cost to
   roughly a third. Opus is the default because it writes explanations better and that
   is the entire product here, but this is your call to make, not the template's.

Change any of them in `samconfig.toml` and re-run `sam deploy`.

---

## Tearing it down

```bash
sam delete
```

This removes the stack but **deliberately leaves the DynamoDB table behind** — it is
marked `DeletionPolicy: Retain`, because those rows are the only record of what the
app was asked and re-earning them costs real money. Delete it by hand from the
DynamoDB console when you genuinely want it gone.
