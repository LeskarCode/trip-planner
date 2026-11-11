# Trip Planner

A small **microservice** web application for planning trips: create a trip, add stops per day (city, title, category, cost) and see the costs broken down by city, category and day.

Built with **Flask**, **MongoDB**, **Docker** and **Kubernetes**.

---

## Architecture

```
                 ┌──────────────────────┐
  Browser ──────►│    trip-frontend     │  Flask · port 8080
                 │  HTML pages + /api   │  (LoadBalancer: the only public entry)
                 └──────────┬───────────┘
                            │ REST / JSON  (API_URL=http://trip-api:5000)
                 ┌──────────▼───────────┐
                 │       trip-api       │  Flask · port 5000
                 │  REST API + logic    │  (internal only)
                 └──────────┬───────────┘
                            │ MongoDB protocol  (MONGO_HOST=trip-db)
                 ┌──────────▼───────────┐
                 │       trip-db        │  MongoDB · port 27017
                 │  trips · stops       │  (persistent volume)
                 └──────────────────────┘
```

| Service | Role | Kubernetes object | Scalable |
|---|---|---|---|
| `trip-frontend` | Web pages for people, plus its own small JSON API | Deployment + `LoadBalancer` Service | Yes |
| `trip-api` | REST API, validation and cost summaries | Deployment + internal Service | Yes |
| `trip-db` | MongoDB database (`tripplanner`: `trips`, `stops`) | StatefulSet + 1Gi volume | No (1 replica) |

The services find each other **by name** (Docker/Kubernetes DNS). Their addresses come from environment variables, so the same code runs on a PC, in Docker Compose and in Kubernetes without changes.

---

## Project structure

```
trip-planner/
├── api/
│   ├── app.py              # trip-api: REST endpoints + MongoDB
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── app.py              # trip-frontend: pages + calls to trip-api
│   ├── templates/
│   │   ├── index.html      # list of trips
│   │   └── trip.html       # one trip: stops + cost summary
│   ├── Dockerfile
│   └── requirements.txt
├── kubernetes/
│   └── trip-planner.yaml   # ConfigMap, Services, Deployments, StatefulSet
└── docker-compose.yml
```

---

## trip-api: REST endpoints

| Method | Path | Description | Success |
|---|---|---|---|
| `GET` | `/health` | Liveness/readiness check | 200 |
| `GET` | `/trips` | List all trips | 200 |
| `POST` | `/trips` | Create a trip. Body: `{"name": "...", "currency": "EUR"}` | 201 |
| `DELETE` | `/trips/<trip_id>` | Delete a trip and all its stops | 200 |
| `GET` | `/trips/<trip_id>/stops` | A trip's stops, sorted by day | 200 |
| `POST` | `/trips/<trip_id>/stops` | Add a stop. Body: `{"day": 1, "city": "...", "title": "...", "category": "...", "cost": 25}` | 201 |
| `DELETE` | `/stops/<stop_id>` | Remove a stop | 200 |
| `GET` | `/trips/<trip_id>/summary` | Total cost, plus per city, per category and per day | 200 |

**Error responses** are JSON (`{"error": "..."}`):
- `400`: invalid ID, missing name/city/title, `day` < 1, `cost` < 0, or non-numeric values
- `404`: trip or stop not found

Defaults: `currency` = `EUR` (stored in capitals), `category` = `other`, `cost` = `0`.

### Example

```bash
# Create a trip
curl -X POST http://localhost:5000/trips \
     -H "Content-Type: application/json" \
     -d '{"name": "USA autumn", "currency": "usd"}'

# Add a stop (use the id returned above)
curl -X POST http://localhost:5000/trips/<trip_id>/stops \
     -H "Content-Type: application/json" \
     -d '{"day": 1, "city": "Chicago", "title": "Jazz bar", "category": "bar", "cost": 25}'

# Get the cost summary
curl http://localhost:5000/trips/<trip_id>/summary
```

The summary includes `"served_by"`: the hostname of the API copy that answered. This makes load balancing visible when several replicas run.

---

## trip-frontend

### Pages

| Path | Description |
|---|---|
| `/` | List of trips and a form to create one |
| `/trips/<trip_id>` | One trip: stops and cost summary (two API calls → one page) |

Forms post to `/trips/new`, `/trips/<id>/delete`, `/trips/<id>/stops/new` and `/stops/<id>/delete`. The frontend forwards them to `trip-api` and redirects back (**Post/Redirect/Get**, so refreshing the page never sends a form twice).

HTML forms can only send `GET` and `POST`, so for deletions the frontend receives a `POST` and sends a real `DELETE` to the API.

### JSON API

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness/readiness check |
| `GET` | `/api/trips` | List of trips (proxied from `trip-api`) |
| `GET` | `/api/trips/<trip_id>/plan` | Trip, cost summary **and** stops in one combined answer |

`/api/trips/<trip_id>/plan` follows the **API gateway** pattern: one request from outside, several calls inside, one combined answer back.

---

## Design patterns

- **Microservices with REST:** each service has one job and exposes a REST API.
- **Configuration from the environment:** `MONGO_HOST`, `MONGO_PORT` and `API_URL`, with sensible defaults for local runs.
- **Timeouts (node failure):** the frontend waits at most 3 seconds for the API, and the API at most 3 seconds for MongoDB, instead of hanging.
- **Graceful degradation:** if `trip-api` is down, the pages show a friendly message instead of crashing, and the JSON API returns `503 Service Unavailable` (busy signal).
- **Validation in one place:** only `trip-api` checks the data; the frontend shows the API's error message.
- **Check first, save after:** a stop is validated completely before it is stored, so the database never gets half-wrong data.
- **Health checks and self-healing:** `/health` endpoints are used by Kubernetes readiness and liveness probes.
- **Stateless services, stateful database:** the API and frontend copies keep no state, so they can scale; the data lives in MongoDB on a persistent volume.

---

## Running the application

### 1. Locally (development)

Requirements: Python 3 and a MongoDB server on `localhost:27017`.

```bash
# Terminal 1: API
cd api
pip install -r requirements.txt
python app.py            # http://localhost:5000

# Terminal 2: frontend
cd frontend
pip install -r requirements.txt
python app.py            # http://localhost:8080
```

### 2. Docker Compose

```bash
docker compose up --build
```

Open **http://localhost:8080**. Only the frontend is published; the API and the database are reachable only inside the Compose network. Data is kept in the `trip-db-data` volume.

Stop with `docker compose down` (add `-v` to delete the data too).

### 3. Kubernetes

The manifest uses the images `leskarcode/trip-api:1.0` and `leskarcode/trip-frontend:1.0`. Build them first (for example with `docker compose build`) and make them available to the cluster (push them to a registry, or load them into a local cluster such as minikube or kind).

```bash
kubectl apply -f kubernetes/trip-planner.yaml
kubectl get pods,svc
```

Open the external address of the `trip-frontend` Service on port 8080. On minikube: `minikube service trip-frontend`.

**Scaling demo:**

```bash
kubectl scale deployment trip-api --replicas=3
kubectl scale deployment trip-frontend --replicas=2
```

Reload a trip page or call `/api/trips/<trip_id>/plan` several times and watch `served_by` (and the hostname in the page footer) change between copies.

Remove everything with `kubectl delete -f kubernetes/trip-planner.yaml`. The database volume (PersistentVolumeClaim) is kept until you delete it yourself.

---

## Configuration

| Variable | Used by | Default | Compose / Kubernetes |
|---|---|---|---|
| `MONGO_HOST` | trip-api | `localhost` | `trip-db` |
| `MONGO_PORT` | trip-api | `27017` | `27017` |
| `API_URL` | trip-frontend | `http://localhost:5000` | `http://trip-api:5000` |

In Kubernetes these values come from the `trip-config` ConfigMap.

---

## Tech stack

Python · Flask · gunicorn · Requests · MongoDB (PyMongo) · Docker · Docker Compose · Kubernetes
