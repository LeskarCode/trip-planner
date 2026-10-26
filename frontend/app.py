# ---------------------------------------------------------------------
# 1. IMPORTS
# ---------------------------------------------------------------------
import os            # read environment settings (API_URL)
import socket        # find this container's name (HOSTNAME)

import requests      # CALL other web services (here: the trip-api)
from flask import Flask, jsonify, redirect, render_template, request, url_for
#   Flask           = the web server
#   jsonify         = turn data into JSON (for /health and our own /api)
#   redirect        = tell the browser "now go to this other page"
#   render_template = fill an HTML page (in templates/) with data
#   request         = read what the browser sent (form fields, ?error=...)
#   url_for         = build the address of one of our own routes by name


# ---------------------------------------------------------------------
# 2. WEB SERVER + SETTINGS
# ---------------------------------------------------------------------
app = Flask(__name__)

API_URL = os.environ.get("API_URL", "http://localhost:5000")
#   WHERE the trip-api lives:
#   on my PC      -> http://localhost:5000 (default)
#   Compose/K8s   -> http://trip-api:5000 (found BY NAME, via DNS)
TIMEOUT = 3
#   never wait more than 3 seconds for the API (node failure pattern)
HOSTNAME = socket.gethostname()
#   which copy of the frontend answered -> shown in the page footer


# ---------------------------------------------------------------------
# 3. request_api: ONE helper for EVERY call to the trip-api
#    Returns TWO things: (data, error_message)
#      success -> (the JSON data, None)
#      failure -> (None, a message to show the user)
#
#    Three things can go wrong, each handled:
#    a) API not reachable / too slow  -> friendly message (no crash)
#    b) answer is not valid JSON      -> treat as empty {}
#    c) API answered with 400/404/... -> use the API's own "error" text
# ---------------------------------------------------------------------
def request_api(method, path, **kwargs):
    #   method = "GET", "POST", "DELETE"
    #   path   = e.g. "/trips"
    #   **kwargs = any extra options, e.g. json={...} for a POST body
    endpoint = API_URL + path

    try:
        response = requests.request(
            method,
            endpoint,
            timeout=TIMEOUT,
            **kwargs
        )
    except requests.RequestException:
        # (a) connection refused, timeout, name not found, ...
        return None, "The trip service is not reachable right now. Please try again."

    try:
        response_data = response.json()       # read the answer as JSON
    except ValueError:
        response_data = {}                    # (b) not JSON -> empty

    if response.status_code >= 400:           # (c) 4xx/5xx = something went wrong
        error_message = response_data.get(
            "error",
            f"API error {response.status_code}"
        )
        return None, error_message

    return response_data, None                # all fine


# ---------------------------------------------------------------------
# 4. HEALTH: "Are you alive?" (for Kubernetes / testing)
# ---------------------------------------------------------------------
@app.route("/health")
def health_check():
    return jsonify({"status": "ok", "service": "trip-frontend"})


# ---------------------------------------------------------------------
# 5. PAGES (HTML for people)
# ---------------------------------------------------------------------

# --- 5a. "/" : the list of trips -------------------------------------
#   browser -> GET / -> frontend calls API: GET /trips -> fills index.html
@app.route("/")
def home_page():
    trips, error = request_api("GET", "/trips")

    page_error = error or request.args.get("error")

    return render_template(
        "index.html",
        trips=trips or [],            # None (on error) -> empty list
        error=page_error,
        #   show either this call's error, or an error passed in the
        #   address after a redirect (e.g. "/?error=name+is+required")
        hostname=HOSTNAME,
    )


# --- 5b. "Create" button on the trips page ---------------------------
#   The HTML form sends: name, currency  (as FORM fields, not JSON)
#   We forward them to the API as JSON, then send the browser back to "/".
@app.route("/trips/new", methods=["POST"])
def create_trip():
    trip_data = {
        "name": request.form.get("name"),
        "currency": request.form.get("currency"),
    }

    _, error = request_api("POST", "/trips", json=trip_data)
    #   "_" = "I don't need this value" (the created trip)

    if error:
        return redirect(url_for("home_page", error=error))

    return redirect(url_for("home_page"))
    #   REDIRECT after a form = "Post/Redirect/Get" pattern:
    #   the browser reloads "/" with a GET, so pressing F5 later
    #   does NOT send the form again (no duplicate trips).

# --- 5c. Delete a trip -----------------------------------------------
@app.route("/trips/<trip_id>/delete", methods=["POST"])
def remove_trip(trip_id):
    _, error = request_api("DELETE", f"/trips/{trip_id}")

    if error:
        return redirect(url_for("home_page", error=error))

    return redirect(url_for("home_page"))

# --- 5d. One trip's page ---------------------------------------------
#   ONE browser request -> TWO API calls (summary + stops) -> trip.html
#
#   Browser          frontend                 trip-api
#     │ GET /trips/X  │                          │
#     │──────────────►│ GET /trips/X/summary ───►│
#     │               │◄──────────── totals ─────│
#     │               │ GET /trips/X/stops ─────►│
#     │               │◄──────────── stops ──────│
#     │◄── HTML page ─│                          │
@app.route("/trips/<trip_id>")
def show_trip(trip_id):
    summary, summary_error = request_api(
        "GET",
        f"/trips/{trip_id}/summary"
    )

    stops, stops_error = request_api(
        "GET",
        f"/trips/{trip_id}/stops"
    )
    #   f"...{trip_id}..." = put the variable's value into the text

    page_error = summary_error or stops_error or request.args.get("error")

    return render_template(
        "trip.html",
        trip_id=trip_id,
        summary=summary,
        stops=stops or [],
        error=page_error,
        #   first error found, if any
        hostname=HOSTNAME,
    )


# --- 5e. "Add" button: add a stop ------------------------------------
@app.route("/trips/<trip_id>/stops/new", methods=["POST"])
def create_stop(trip_id):
    form = request.form               # the fields typed in the browser

    stop_data = {
        "day": form.get("day"),
        "city": form.get("city"),
        "title": form.get("title"),
        "category": form.get("category"),
        "cost": form.get("cost") or 0,   # empty cost field -> 0
    }

    _, error = request_api(
        "POST",
        f"/trips/{trip_id}/stops",
        json=stop_data
    )

    #   NOTE: the frontend does NOT check the data itself; the API does
    #   (one place for the rules). The API's error message is shown.
    if error:
        return redirect(
            url_for("show_trip", trip_id=trip_id, error=error)
        )

    return redirect(url_for("show_trip", trip_id=trip_id))


# --- 5f. "Delete" button: remove a stop ------------------------------
#   HTML forms can only send GET or POST, not DELETE.
#   So the browser POSTs to the frontend, and the frontend sends a
#   real DELETE to the API.
@app.route("/stops/<stop_id>/delete", methods=["POST"])
def remove_stop(stop_id):
    trip_id = request.form.get("trip_id")    # hidden field: where to go back

    _, error = request_api("DELETE", f"/stops/{stop_id}")

    if error:
        return redirect(url_for("show_trip", trip_id=trip_id, error=error))

    return redirect(url_for("show_trip", trip_id=trip_id))


# ---------------------------------------------------------------------
# 6. THE FRONTEND'S OWN REST API (JSON) - for assignment rule 3
#    "each microservice must implement a REST API"
# ---------------------------------------------------------------------

# --- 6a. GET /api/trips: list of trips as JSON -----------------------
@app.route("/api/trips", methods=["GET"])
def get_api_trips():
    trips, error = request_api("GET", "/trips")

    if error:
        return jsonify({"error": error}), 503
        #   503 = "service unavailable": the BUSY SIGNAL pattern
        #   (I'm alive, but can't help right now because the API is down)

    return jsonify(trips)


# --- 6b. GET /api/trips/<id>/plan: everything in ONE answer ----------
#   API GATEWAY style (Taibi et al.): one request from outside,
#   several calls inside, ONE combined answer back.
@app.route("/api/trips/<trip_id>/plan", methods=["GET"])
def get_trip_plan(trip_id):
    summary, summary_error = request_api(
        "GET",
        f"/trips/{trip_id}/summary"
    )

    stops, stops_error = request_api(
        "GET",
        f"/trips/{trip_id}/stops"
    )

    error = summary_error or stops_error

    if error:
        if error == "trip not found":
            status_code = 404
        elif error == "invalid id":
            status_code = 400
        else:
            status_code = 503
        return jsonify({"error": error}), status_code

    trip_plan = {
        "trip": summary["trip"],
        "total": summary["total"],
        "per_city": summary["per_city"],
        "per_category": summary["per_category"],
        "per_day": summary["per_day"],
        "stops": stops,
        "served_by": HOSTNAME,        # which FRONTEND copy answered
    }

    return jsonify(trip_plan)


# ---------------------------------------------------------------------
# 7. START-UP (only with "python app.py" on my PC)
#    In Docker/Kubernetes, gunicorn runs "app:app" instead.
#    Port 8080 here, so it doesn't clash with the API's 5000.
#    MUST stay the LAST lines of the file.
# ---------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)