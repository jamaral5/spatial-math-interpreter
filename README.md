# Spatial Math — AR Math Interpreter

The cloud half of [Spatial Math](../README.md), a Unity 3D graphing calculator aimed at
AR headsets.

Right-click a point on a plotted surface and this service explains, in plain language,
what the surface is doing there — whether the ground is level, which way it curves,
where the steepest climb points, and what the classification actually means.

> **Status: not yet deployed.** All the code is written and the dependency-free logic
> has been exercised, but nothing has run against real AWS infrastructure and the test
> suite has never been executed, because there is no AWS account yet. Start with
> [`docs/getting-started.md`](docs/getting-started.md).

---

## The one thing to understand

**Unity computes every number. The model computes none.**

The C# math engine evaluates the surface, estimates both partial derivatives and all
three second partials, computes the discriminant, and reaches a classification. Only
then does anything leave the machine — and what leaves is the *finished answer*, not
the problem.

The model's entire job is to put those numbers into words. It is never asked to
calculate, because language models are unreliable arithmetic engines and reliable
explainers, and in a maths teaching tool a confident wrong answer is worse than no
answer at all.

That rule is enforced structurally in four places, not just asked for politely — see
[`docs/architecture.md`](docs/architecture.md).

---

## Layout

```
AWS/
├── template.yaml              SAM: API Gateway + Lambda + DynamoDB
├── samconfig.toml             deploy settings (no secrets)
├── env.json                   env vars for `sam local invoke`
│
├── src/interpret/             the Lambda
│   ├── app.py                 handler — validate, cache, prompt, model, cache
│   ├── payload.py             request contract + untrusted-input validation
│   ├── prompt.py              the system prompt and response schema
│   ├── bedrock_client.py      the Bedrock call, error mapping
│   ├── store.py               DynamoDB cache + history
│   └── config.py              environment
│
├── contracts/                 the interface between the two repositories
│   ├── interpret-request.schema.json
│   ├── interpret-response.schema.json
│   └── examples/              four real payloads, used by the smoke test
│
├── unity-client/              C# to copy into Assets/Scripts/ at Phase 2
│   ├── SurfaceAnalyzer.cs     the mathematics
│   ├── InterpretDto.cs        the wire format
│   ├── MathInterpreterClient.cs   the networking
│   └── InterpretationPanel.cs     the UI
│
├── tests/                     pytest — written, never run
├── scripts/                   AWS CLI wrappers (.sh and .ps1)
└── docs/
    ├── getting-started.md     ← start here
    ├── architecture.md        design decisions and why
    └── roadmap.md             phases, exit tests, known gaps
```

---

## Quick start

You need an AWS account first, and one Playground call to enable the model — that is
Phase 0. [`docs/getting-started.md`](docs/getting-started.md) walks through it from
nothing.

Once tooling exists:

```bash
./scripts/list-models.sh us-east-1
```

Put a model id it prints into `samconfig.toml`, then:

```bash
sam build && sam deploy
```

```bash
./scripts/smoke-test.sh
```

Run the smoke test **twice** — the first response should say `"cached": false` and the
second `"cached": true`.

From PowerShell instead:

```powershell
. .\scripts\Spatial-Math.ps1
Get-SpatialMathModels
Invoke-SpatialMathSmokeTest
```

Then wire up Unity: [`unity-client/README.md`](unity-client/README.md).

---

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest
```

They cover request validation, prompt construction, and the handler with Bedrock and
DynamoDB stubbed — no test in the suite touches the network.

**They have not been run.** They are written against the real modules and the fixtures
use values the C# `SurfaceAnalyzer` actually produces, but `pytest` is not installed on
this machine yet, so treat a green run as the first thing to confirm rather than
something already established.

The pure-logic parts *have* been exercised directly — every example in `contracts/`
round-trips through the real `payload.py`, and the injection, NaN, infinity and bad-shape
rejections all behave as intended.

---

## Costs

Everything except Bedrock is effectively free at this scale. Bedrock is the only
usage-billed piece, at roughly **1–2 cents per uncached click on Opus** and well under
a cent on Sonnet or Haiku — but check <https://aws.amazon.com/bedrock/pricing/> for
AWS's own rates rather than trusting that.

Three guards are already in place:

- **A hard monthly quota** on the API key (`MonthlyQuota=2000`), so a leaked key or a
  runaway loop caps out rather than running up an open-ended bill.
- **A DynamoDB cache** in front of the model, keyed to two decimal places so that
  clicks which look identical on screen resolve to the same entry.
- **`Effort=low`**, which is the correct setting on the merits here rather than a
  compromise: the endpoint restates numbers it was handed.

The quota is the lever worth thinking about — 2,000 uncached Opus calls is $20–30 if
you ever used all of it. `MonthlyQuota=300` in `samconfig.toml` is still far more than
a demo needs. See the cost section of
[`docs/getting-started.md`](docs/getting-started.md).

**Set an AWS budget alarm before your first deploy.** It is ten minutes and it is the
difference between noticing a mistake immediately and noticing it on a statement.

Plan on eventually proposing to UF math department as a supplemental learning material

---

## Why this is a separate repository

The Unity project is a Unity project: `.gitignore` tuned for `Library/` and `Temp/`,
Unity's own `.gitattributes` for asset merging, and a history that is mostly scenes and
prefabs. The backend is a Python and CloudFormation project with an entirely different
toolchain and release cadence.

Keeping them apart means the Unity repository stays clean while the backend is under
construction, the backend can be deployed and rolled back without touching the game,
and neither history is cluttered by the other. The `unity-client/` folder is the seam:
the C# that bridges the two lives here until it is ready to be copied across.
