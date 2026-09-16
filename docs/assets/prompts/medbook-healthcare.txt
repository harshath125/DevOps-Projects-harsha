# ROLE

You are a senior Java/Spring Boot engineer. Build a complete, runnable backend application for a small healthcare clinic. You are the DEVELOPER. A separate DevOps engineer will handle everything about deployment, containers, cloud, CI/CD and security — that is not your job here.

# PRODUCT: MediBook

A clinic appointment-booking service. Patients book, reschedule and cancel appointments with doctors. Receptionists manage the schedule. A small reporting endpoint gives daily counts.

# FUNCTIONAL REQUIREMENTS

Implement these REST endpoints (JSON only, no server-rendered HTML, no mobile app):

1. `POST /api/auth/login` — email + password, returns a short-lived JWT. Also `POST /api/auth/register` restricted to a seed role.
2. `GET /api/doctors` — list doctors with `?specialty=&page=&size=`.
3. `GET /api/doctors/{id}/availability?date=` — 30-minute slots between the doctor's working hours, excluding already-booked slots.
4. `POST /api/appointments` — body `{patientId, doctorId, scheduledAt, reason}`; reject double-booking of the same doctor with a `409`; validate that the slot is in the future and inside working hours.
5. `GET /api/appointments?patientId=|doctorId=&from=&to=&page=&size=` — paginated, sorted by `scheduledAt`.
6. `PATCH /api/appointments/{id}` — reschedule (same slot rules) or cancel (`status=CANCELLED`).
7. `GET /api/appointments/{id}` — only the owning patient, the doctor, or role `RECEPTIONIST`; otherwise `403`.
8. `POST /api/appointments/{id}/no-show` and `POST /api/appointments/{id}/complete` — status transitions with validation.
9. `GET /api/reports/day?date=` — counts of booked / completed / cancelled / no-show per doctor, for one day.
10. `GET /actuator/health`, `GET /actuator/health/liveness`, `GET /actuator/health/readiness`, `GET /actuator/metrics`, `GET /swagger-ui.html` (OpenAPI).

Data model (JPA entities with Flyway migrations, no `ddl-auto` in production):
`Patient(id, firstName, lastName, email unique, phone, dob, passwordHash, role)` · `Doctor(id, name, specialty, workStart, workEnd, room)` · `Appointment(id, patient, doctor, scheduledAt, durationMinutes default 30, status enum SCHEDULED|COMPLETED|CANCELLED|NO_SHOW, reason, version, createdAt, updatedAt)` with a unique constraint on `(doctor_id, scheduled_at)`.

# NON-FUNCTIONAL REQUIREMENTS (these matter as much as the features)

- **Configuration via environment variables only.** Every setting must be overridable by env var (Spring's relaxed binding is fine): `DB_URL`, `DB_USER`, `DB_PASSWORD`, `JWT_SECRET` (min 32 chars, fail fast if missing), `SERVER_PORT` (default 8080), `CORS_ALLOWED_ORIGINS`, `LOG_LEVEL`, `APP_TIMEZONE`. Provide `application.yml` with safe local defaults and **no real credentials**.
- **Database:** PostgreSQL 16. Flyway migrations under `src/main/resources/db/migration` (`V1__init.sql`, `V2__seed_doctors.sql`). Include a seed script runnable with `psql -f` for local use.
- **Health:** actuator health with a DB check; expose `/actuator/health/liveness` and `/actuator/health/readiness` via `management.endpoint.health.probes.enabled=true`.
- **Errors:** RFC-7807 style problem responses with a stable `code` field (`SLOT_TAKEN`, `VALIDATION_FAILED`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`), plus an X-Correlation-Id echoed and generated if absent.
- **Logging:** JSON lines to **stdout** (no file appenders, no rolling files): timestamp, level, logger, message, `correlation_id`, `patient_id_masked`. Never log passwords, tokens or full patient identifiers.
- **Validation & security basics:** Bean Validation on all inputs; BCrypt password hashing; JWT HS256 with a 30-minute expiry; role-based authorization (`PATIENT`, `DOCTOR`, `RECEPTIONIST`); parameterised queries only; rate limit on `/api/auth/login` (5/min per IP, in-memory is fine); `server.shutdown=graceful` with a 20 s timeout; `spring.mvc.problemdetails.enabled=true`.
- **Performance:** p99 < 300 ms for `GET /api/appointments` with 100 k appointments; add indexes for the queries you actually write.
- **Tests:** JUnit 5 + AssertJ; unit tests for slot-availability and double-booking rules; `@SpringBootTest` slice tests for each controller; one Testcontainers-based integration test against real PostgreSQL; `mvn verify` must pass with the tests wired to a container (use Testcontainers so no local DB is required).
- **Build:** Maven wrapper committed (`mvnw`, `.mvn/`), Java 17, Spring Boot 3.2.x, single module. `target/` must not be committed. Package name `com.clinic.medibook`. Main class `MediBookApplication`.
- **Docs:** a root `README.md` with: what it is, how to run locally (`docker run` for Postgres + `./mvnw spring-boot:run`), the full environment-variable table, the endpoint list, how to run the tests, and a `docs/OPERATIONS.md` you would hand to whoever deploys it: startup command, port, health endpoints, migration command, what to check if the DB connection fails, and known limitations.

# DELIVERY DEFINITION (this is the handoff)

- Repository layout: standard Maven, `src/main/java`, `src/main/resources/db/migration`, `src/test/java`, `README.md`, `docs/`, `LICENSE`.
- `docker-compose.yml` **for local development only** (app + Postgres, so a developer can run it) — no production Dockerfile, no Kubernetes manifests, no cloud files. If you think a Dockerfile is useful for development, add `docker-compose.dev.yml` and say so in the README.
- `.editorconfig`, `.gitignore` (Maven/IDE/logs/`.env`), `.env.example`.
- Seed data: 6 doctors, 3 patients, one receptionist, ~40 appointments across next week so screens and reports are not empty. Password for seeds documented in the README (`ChangeMe123!`).
- No TODOs, no stubbed methods, no `println` debugging, no hard-coded `localhost` or credentials in code.

# HARD CONSTRAINTS — READ CAREFULLY

You are **only** the developer. Do NOT do any of the following, even if you think it helps:

- No Dockerfile, no `.dockerignore`, no container build or registry work.
- No Terraform, CloudFormation, Bicep, Pulumi or any infrastructure code.
- No AWS / Azure / GCP resources, SDKs, CLI scripts, IAM policies, security groups, load balancers, RDS or S3 code.
- No Jenkinsfile, no `.github/workflows`, no GitLab CI, no Argo/GitOps files, no Helm charts, no Kubernetes or Docker Compose *production* manifests.
- No SonarQube, Trivy, Checkov, OWASP ZAP configuration, no security-gate scripts, no signing or SBOM work.
- No monitoring or alerting setup (no Prometheus scrape config, no Grafana dashboards, no CloudWatch/Alertmanager config).
- No Nginx/Caddy/Traefik reverse-proxy configuration, no TLS certificates, no systemd units, no shell “deploy.sh” scripts.
- Do not create GitHub repositories, branches, releases or tags; do not run `git init` and do not push anywhere. Return the files; the human owns the repository.

If something in the requirements seems to need one of those, implement the application-side part only (e.g. expose a metrics endpoint) and write a short note in `docs/OPERATIONS.md` for the DevOps engineer about what they will need to decide.

# OUTPUT FORMAT

1. First, a short plan (max 15 lines) of the files you will create.
2. Then the code, file by file, each with a path header so it can be written to disk verbatim.
3. Then `README.md` and `docs/OPERATIONS.md`.
4. Then a final section titled `DEVOPS HANDOFF NOTES` listing: the exact build command, the exact run command, every environment variable with its default, the port, the health endpoints, the migration command, the test command, external dependencies (PostgreSQL only), and anything you had to make an assumption about.

Keep the whole implementation focused: roughly 25–40 files. Prefer boring, standard Spring Boot over clever abstractions.
