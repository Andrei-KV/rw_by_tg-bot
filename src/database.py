import json
import logging
from datetime import datetime, timedelta
from typing import Any, Literal, Sequence, overload

import pg8000
import sqlalchemy
from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy.engine import Row

from src.config import settings

# initialize Connector object
connector = Connector()


def getconn():
    conn = connector.connect(
        settings.DB_INSTANCE_NAME,
        "pg8000",
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        db=settings.DB_NAME,
        ip_type=IPTypes.PUBLIC,
    )
    return conn


# The Cloud SQL Python Connector can be used with SQLAlchemy
db_pool = sqlalchemy.create_engine(
    "postgresql+pg8000://",
    creator=getconn,
)


@overload
def execute_db_query(
    query: str,
    params: dict | None = None,
    *,
    fetchone: Literal[True],
    fetchall: Literal[False] = False,
    commit: bool = False,
) -> Row[Any] | None:
    ...


@overload
def execute_db_query(
    query: str,
    params: dict | None = None,
    *,
    fetchone: Literal[False] = False,
    fetchall: Literal[True],
    commit: bool = False,
) -> Sequence[Row[Any]]:
    ...


@overload
def execute_db_query(
    query: str,
    params: dict | None = None,
    *,
    fetchone: Literal[False] = False,
    fetchall: Literal[False] = False,
    commit: bool = True,
) -> None:
    ...


def execute_db_query(
    query, params=None, fetchone=False, fetchall=False, commit=False
):
    """
    Executes a SQL query using a connection from the pool.
    """
    with db_pool.connect() as conn:
        try:
            if commit:
                trans = conn.begin()

            result = conn.execute(sqlalchemy.text(query), params or {})

            if fetchone:
                row = result.fetchone()
                if commit:
                    trans.commit()
                return row
            elif fetchall:
                rows = result.fetchall()
                if commit:
                    trans.commit()
                return rows

            if commit:
                trans.commit()

        except Exception as e:
            if commit:
                trans.rollback()
            logging.error(f"Database error in execute_db_query: {e}")
            raise


def check_db_connection():
    """Checks the database connection."""
    try:
        with db_pool.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        logging.debug("Database connection successful")
        return True
    except Exception as e:
        logging.error(f"Database connection failed: {e}")
        return False


def create_tables():
    """Creates all necessary tables in the database if they don't exist."""
    commands = (
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id BIGINT PRIMARY KEY
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS routes (
            route_id SERIAL PRIMARY KEY,
            city_from VARCHAR(255),
            city_to VARCHAR(255),
            date DATE,
            url VARCHAR(512) UNIQUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trains (
            train_id SERIAL PRIMARY KEY,
            route_id INTEGER REFERENCES routes(route_id) ON DELETE CASCADE,
            train_number VARCHAR(255),
            time_depart VARCHAR(10),
            time_arriv VARCHAR(10),
            UNIQUE(route_id, train_number, time_depart, time_arriv)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tracking (
            tracking_id SERIAL PRIMARY KEY,
            chat_id BIGINT REFERENCES users(chat_id) ON DELETE CASCADE,
            train_id INTEGER REFERENCES trains(train_id) ON DELETE CASCADE,
            json_ticket_dict JSONB,
            next_check_at TIMESTAMP WITH TIME ZONE,
            UNIQUE(chat_id, train_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
            chat_id BIGINT PRIMARY KEY,
            data JSONB,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    try:
        with db_pool.connect() as conn:
            trans = conn.begin()
            for command in commands:
                conn.execute(sqlalchemy.text(command))
            trans.commit()
        logging.info("Database tables created or already exist.")
    except Exception as e:
        logging.error(f"Error creating database tables: {e}")
        raise


def add_user_db(chat_id):
    """Adds a new user to the database."""
    execute_db_query(
        "INSERT INTO users (chat_id) VALUES (:chat_id) "
        "ON CONFLICT (chat_id) DO NOTHING",
        {"chat_id": chat_id},
        commit=True,
    )
    logging.info(f"User {chat_id} added to database")


def add_route_db(city_from, city_to, date, url):
    """Adds a new route to the database."""
    execute_db_query(
        "INSERT INTO routes (city_from, city_to, date, url) "
        "VALUES (:city_from, :city_to, :date, :url) "
        "ON CONFLICT (url) DO NOTHING",
        {"city_from": city_from, "city_to": city_to, "date": date, "url": url},
        commit=True,
    )
    logging.info(f"Route {city_from}-{city_to}-{date} added to database")


def add_trains_db_batch(trains_data, url):
    """Adds a list of trains to the database in a single batch."""
    result = execute_db_query(
        "SELECT route_id FROM routes WHERE url = :url",
        {"url": url},
        fetchone=True,
    )
    if result is None:
        logging.warning(f"No route found for URL: {url}")
        return
    route_id = result[0]

    with db_pool.connect() as conn:
        trans = conn.begin()
        try:
            insert_statement = sqlalchemy.text(
                "INSERT INTO trains (route_id, train_number, "
                "time_depart, time_arriv) "
                "VALUES (:route_id, :train_number, "
                ":time_depart, :time_arriv) "
                "ON CONFLICT (route_id, train_number, "
                "time_depart, time_arriv) DO NOTHING"
            )

            params_list = [
                {
                    "route_id": route_id,
                    "train_number": train["train"],
                    "time_depart": train["time_depart"],
                    "time_arriv": train["time_arriv"],
                }
                for train in trains_data
            ]

            if not params_list:
                return

            conn.execute(insert_statement, params_list)
            trans.commit()
            logging.info(
                f"{len(params_list)} trains for route_id: {route_id} "
                f"processed for the database."
            )
        except Exception as e:
            trans.rollback()
            logging.error(f"Database error in add_trains_db_batch: {e}")
            raise


def add_tracking_db(chat_id, train_selected, ticket_dict, url):
    """Adds a new train to the tracking list."""
    result = execute_db_query(
        "SELECT route_id FROM routes WHERE url = :url",
        {"url": url},
        fetchone=True,
    )
    if result is None:
        logging.warning(f"No route found for URL: {url}")
        return
    route_id = result[0]

    result = execute_db_query(
        "SELECT train_id FROM trains "
        "WHERE route_id = :route_id AND train_number = :train_number",
        {"route_id": route_id, "train_number": train_selected},
        fetchone=True,
    )
    if not result:
        logging.warning(
            f"Train not found for route_id={route_id}, number={train_selected}"
        )
        return
    train_id = result[0]

    json_ticket_dict = json.dumps(ticket_dict)

    execute_db_query(
        "INSERT INTO tracking (chat_id, train_id, json_ticket_dict, next_check_at) "
        "VALUES (:chat_id, :train_id, :json_ticket_dict, NOW()) "
        "ON CONFLICT (chat_id, train_id) DO NOTHING",
        {
            "chat_id": chat_id,
            "train_id": train_id,
            "json_ticket_dict": json_ticket_dict,
        },
        commit=True,
    )
    logging.info(f"Train_id: {train_id} added to db_tracking_list")


def get_trains_list_db(url):
    """Gets the list of trains for a given route from the database."""
    result = execute_db_query(
        "SELECT route_id FROM routes WHERE url = :url",
        {"url": url},
        fetchone=True,
    )
    if not result:
        logging.warning(f"No route found for URL: {url}")
        return []
    route_id = result[0]
    trains_list = execute_db_query(
        "SELECT train_number, time_depart, time_arriv FROM trains "
        "WHERE route_id = :route_id ORDER BY time_depart",
        {"route_id": route_id},
        fetchall=True,
    )
    return trains_list


def get_loop_data_list(chat_id, train_tracking, url):
    """Gets data for the tracking loop."""
    query = """
        SELECT r.route_id, t.train_id,
        EXISTS (
            SELECT 1 FROM tracking tr
            WHERE tr.train_id = t.train_id AND tr.chat_id = :chat_id
        ) AS is_tracked
        FROM routes r
        JOIN trains t ON r.route_id = t.route_id
        WHERE r.url = :url AND t.train_number = :train_number
        LIMIT 1
    """
    resp = execute_db_query(
        query,
        {"chat_id": chat_id, "url": url, "train_number": train_tracking},
        fetchone=True,
    )
    if not resp:
        logging.warning(
            f"No matching train or route found for chat_id: {chat_id},\
url: {url}, train: {train_tracking}"
        )
        return None

    count_result = execute_db_query(
        "SELECT COUNT(*) FROM tracking WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
        fetchone=True,
    )
    count = count_result[0] if count_result else 0
    result = {
        "route_id": resp[0],
        "train_id": resp[1],
        "status_exist": resp[2],
        "count": count,
    }
    return result


def get_fresh_loop(chat_id, train_id):
    """Gets fresh data from the tracking table."""
    result = execute_db_query(
        "SELECT json_ticket_dict FROM tracking "
        "WHERE chat_id = :chat_id AND train_id = :train_id",
        {"chat_id": chat_id, "train_id": train_id},
        fetchone=True,
    )
    if result and result[0] is not None:
        json_data = result[0]
        if isinstance(json_data, dict):
            return json_data
        if isinstance(json_data, str):
            try:
                if not json_data:  # Handle empty string case
                    return {}
                return json.loads(json_data)
            except json.JSONDecodeError:
                logging.warning(
                    f"Invalid JSON in tracking data for chat_id {chat_id}, "
                    f"train_id {train_id}. Data: '{json_data}'"
                )
                return {}
    return {}


def get_track_list(chat_id):
    """Gets the list of tracked trains for a user."""
    track_list = execute_db_query(
        """
        SELECT tracking_id, t.train_number,
               r.city_from, r.city_to, r.date, t.time_depart
        FROM tracking tr
        JOIN trains t ON tr.train_id = t.train_id
        JOIN routes r ON t.route_id = r.route_id
        WHERE tr.chat_id = :chat_id
        """,
        {"chat_id": chat_id},
        fetchall=True,
    )
    return track_list


def del_tracking_db(chat_id, train_id):
    """Deletes a tracked train from the database."""
    execute_db_query(
        "DELETE FROM tracking "
        "WHERE chat_id = :chat_id AND train_id = :train_id",
        {"chat_id": chat_id, "train_id": train_id},
        commit=True,
    )


def update_tracking_loop(json_ticket_dict, chat_id, train_id):
    """Updates the tracking table in a loop."""
    execute_db_query(
        "UPDATE tracking SET json_ticket_dict = :json_ticket_dict "
        "WHERE chat_id = :chat_id AND train_id = :train_id",
        {
            "json_ticket_dict": json_ticket_dict,
            "chat_id": chat_id,
            "train_id": train_id,
        },
        commit=True,
    )


def check_user_exists(chat_id):
    """Checks if a user exists in the database."""
    result = execute_db_query(
        "SELECT EXISTS(SELECT 1 FROM users WHERE chat_id = :chat_id)",
        {"chat_id": chat_id},
        fetchone=True,
    )
    return bool(result[0]) if result else False


def get_due_trackings():
    """Gets trackings that are due for a check."""
    rows = execute_db_query(
        """
        SELECT
            t.tracking_id,
            t.chat_id,
            tr.train_number,
            t.train_id,
            tr.route_id,
            r.url,
            r.date,
            tr.time_depart
        FROM tracking t
        JOIN trains tr ON t.train_id = tr.train_id
        JOIN routes r ON tr.route_id = r.route_id
        WHERE t.next_check_at <= NOW()
        """,
        fetchall=True,
    )
    return rows


def update_next_check_time(tracking_id, next_check_at):
    """Updates the next check time for a specific tracking entry."""
    execute_db_query(
        "UPDATE tracking SET next_check_at = :next_check_at "
        "WHERE tracking_id = :tracking_id",
        {"next_check_at": next_check_at, "tracking_id": tracking_id},
        commit=True,
    )


def cleanup_expired_routes_db():
    """Deletes expired routes from the database."""
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    expired_routes = execute_db_query(
        "SELECT route_id FROM routes WHERE date < :yesterday",
        {"yesterday": yesterday},
        fetchall=True,
    )
    if expired_routes:
        route_ids = [r[0] for r in expired_routes]
        execute_db_query(
            "DELETE FROM tracking WHERE train_id IN (SELECT train_id "
            "FROM trains WHERE route_id = ANY(:route_ids))",
            {"route_ids": route_ids},
            commit=True,
        )
        execute_db_query(
            "DELETE FROM trains WHERE route_id = ANY(:route_ids)",
            {"route_ids": route_ids},
            commit=True,
        )
        execute_db_query(
            "DELETE FROM routes WHERE route_id = ANY(:route_ids)",
            {"route_ids": route_ids},
            commit=True,
        )
        logging.info(f"Deleted {len(expired_routes)} expired routes")


def stop_tracking_by_id_db(tracking_id):
    """Stops tracking by tracking_id."""
    execute_db_query(
        "DELETE FROM tracking WHERE tracking_id = :tracking_id",
        {"tracking_id": tracking_id},
        commit=True,
    )


def stop_all_tracking_for_user_db(chat_id):
    """Stops all tracking for a user."""
    execute_db_query(
        "DELETE FROM tracking WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
        commit=True,
    )
    execute_db_query(
        "DELETE FROM users WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
        commit=True,
    )
    logging.info(f"Bot stopped for chat_id: {chat_id}. Tracking list cleared.")


def get_departure_date_db(train_id):
    """Gets the departure date for a given train from the database."""
    resp_db = execute_db_query(
        "SELECT r.date FROM trains t JOIN routes r ON t.route_id = r.route_id "
        "WHERE train_id = :train_id",
        {"train_id": train_id},
        fetchone=True,
    )
    if resp_db:
        return datetime.strptime(str(resp_db[0]), "%Y-%m-%d").date()
    return None


def get_user_session(chat_id):
    """Gets the session data for a user."""
    logging.debug(f"FLAG start 11 get_user_session {chat_id}")
    result = execute_db_query(
        "SELECT data FROM user_sessions WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
        fetchone=True,
    )
    return result.data if result else {}


def update_user_session(chat_id, data):
    """Updates the session data for a user."""
    execute_db_query(
        """
        INSERT INTO user_sessions (chat_id, data)
        VALUES (:chat_id, :data)
        ON CONFLICT (chat_id) DO UPDATE SET
            data = EXCLUDED.data,
            updated_at = CURRENT_TIMESTAMP
        """,
        {"chat_id": chat_id, "data": json.dumps(data)},
        commit=True,
    )


def delete_user_session(chat_id):
    """Deletes the session data for a user."""
    execute_db_query(
        "DELETE FROM user_sessions WHERE chat_id = :chat_id",
        {"chat_id": chat_id},
        commit=True,
    )
