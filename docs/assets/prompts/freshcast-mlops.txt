You are a senior ML engineer. Build **FreshCast** — a small demand-forecasting model and its inference service, for a **foundation-level MLOps** project. Scope: a training script, a serialized model, a FastAPI inference service, and a data contract. A DevOps engineer owns GCP, containers, pipelines and monitoring. **Do not build any MLOps platform.**

## Business context

A grocery chain forecasts next-day unit demand per store × product family so replenishment can be planned. Accuracy matters more than elegance; the model must be explainable enough that an operations analyst trusts it.

## Stack (do not substitute)

Python 3.12 · pandas + numpy · **scikit-learn 1.4** (`GradientBoostingRegressor` baseline and a `Ridge` fallback) · joblib for the artefact · FastAPI + Pydantic v2 for the serving layer · `pytest` · `ruff` + `black`. No deep learning, no PyTorch, no XGBoost, no LLM, no feature store, no experiment tracker.

## Deliverables

1. **Data contract** — `docs/DATA-CONTRACT.md` plus `data/schema.json`: `daily_sales.csv` columns `date(YYYY-MM-DD), store_id(str), family(str), weekly_sales(float)`, `stores.csv` `store_id, city, region, cluster(int)`, `features.csv` `date, store_id, family, is_holiday(int), promo_week(int), day_of_week(int)`. State types, nullability, ranges, and what makes a row *invalid*.
2. **Dataset generator** — `python -m tools.make_data --days 900 --stores 12 --families 4 --out data/` producing deterministic synthetic history (`--seed`) with weekly seasonality, a holiday effect, a promo bump, one distribution-shift event in the last 60 days (deliberate, documented), and ~0.5 % malformed rows on purpose (nulls/negatives) so validation has something to reject. Also write a `holdout.csv` covering the last 14 days (the model must never see it during training — assert that).
3. **Training pipeline** — `train.py` (CLI: `--data-dir --model-out --report-out`) with: validation → feature engineering (lags 1/7, rolling 7/28 mean, day-of-week one-hots, holiday/promo flags) → time-ordered split (no shuffling — write a comment explaining why) → Ridge + GradientBoosting, choose by MAPE on a *validation* window → persist `model.joblib` + `metadata.json` (`trained_at`, `git_sha`, `sklearn_version`, feature list + order, metrics, dataset hash) → write `report.json` (MAE/MAPE/RMSE per family, top-5 `permutation_importance`, drift flag comparing train vs recent input statistics).
4. **Inference service** — `serve/` FastAPI on :8000: `POST /v1/predict` {store_id, family, date, optional features} → {predicted_units, model_version, latency_ms, warnings[]} (missing feature → imputed with the rolling mean and a warning; date beyond horizon → `range_warning`); `POST /v1/batch` (≤ 500 rows, one validation error list returned, no partial write); `GET /healthz` (process), `GET /readyz` (model loaded + version matches metadata), `GET /metrics` (Prometheus: `http_request_duration_seconds{route}` histogram, `freshcast_predictions_total{status}`, `freshcast_feature_missing_total{name}`, `freshcast_model_version_info{version,sha}`, `freshcast_drift_psi` gauge).
5. **Validation module** — `freshcast/validate.py`: rejects negative/NaN sales, unknown `store_id`/`family`, dates before 2020-01-01, `is_holiday ∉ {0,1}`; returns a structured error list; used by both training and serving (one implementation, tested once).
6. **Model registry stub** — `models/` directory holding `model.joblib` + `metadata.json`; `python -m tools.promote --from models/ --to models/prod/` copies and writes `promotion.json` (who/when/source hash). This is intentionally primitive: the DevOps engineer replaces it with an artefact store + CI. Document that trade-off in `docs/OPERATIONS.md`.

## Configuration (env-only; `.env.example`; validated at boot)

`PORT=8000`, `MODEL_PATH=/app/models/prod/model.joblib`, `LOG_LEVEL=info`, `LOG_FORMAT=json`, `MAX_BATCH=500`, `PREDICTION_TIMEOUT_MS=800`, `DRIFT_PSI_THRESHOLD=0.2`. The service must run **read-only**: no writes to disk, no retraining, model file mounted at `/app/models/prod` — state that explicitly.

## Operations contract (`docs/OPERATIONS.md`)

How to train (exact commands, expected console output, wall-clock estimate for the default dataset: "about 90 s on 4 vCPU"), how to serve, artefact paths and sizes (model ≈ 3 MB, `metadata.json` fields), memory/CPU profile (inference p95 ≈ 12 ms; training peak RSS ≈ 700 MB — this is why the two run as separate containers), the two ways a deploy can silently break (sklearn version skew between train and serve, feature-order drift) and the assertion in `readyz` that catches each, what to alert on (drift PSI > threshold, feature-missing rate, p95 > 100 ms, readiness 503s), rollback = serve the previous model directory, and why retraining must never be triggered by a request.

## Tests

pytest: validation rules (each rejection), time-ordered split has no leakage (assert max(train date) < min(val date)), deterministic predictions for a fixed seed (`assertAlmostEqual` on a golden file), batch validation error list, `readyz` fails on a version-mismatched metadata, drift metric computed, and one end-to-end train-on-30-days → load → predict → metrics populated. `ruff check`, `black --check`, `pytest` green; `make verify` runs all three.

## Definition of done

`make data && make train && make serve` works from a clean checkout with only documented env vars; `curl -fsS localhost:8000/readyz` returns 200 with `model_version`; `POST /v1/predict` returns a number with a warning when a feature is missing; `report.json` contains MAPE per family and a `drift` flag set to true because of the planted shift; model artefacts are ≤ 10 MB total; `git ls-files | grep -Ei 'dockerfile|kube|tf|terraform|jenkins|actions|helm|dvc|mlflow'` is empty.

## HARD EXCLUSIONS — do not do these

No Dockerfile or image build, no docker-compose for deployment (local deps only), no Kubernetes/Helm/manifests, no Terraform/Bicep/CloudFormation, **no GCP project, service accounts, BigQuery, Vertex AI, Pub/Sub or any cloud resource**, no CI/CD pipelines, no MLOps platform (no MLflow, DVC, Kubeflow, Airflow — the artefact stub above is deliberately primitive), no model registry service, no feature store, no monitoring stack or dashboards, no deployment of any kind. Model + serving code + data contract only; the DevOps engineer packages and operates it.

## Handover

Repo `freshcast`, 4–6 commits, tag `v1.0.0`, push to GitHub, then report: URL, tag, train/serve commands, port, env vars, artefact paths and sizes, health paths, test commands, and the two silent-failure modes you most want a pipeline to guard.
