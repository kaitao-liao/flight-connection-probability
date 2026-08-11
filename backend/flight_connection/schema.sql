CREATE TABLE IF NOT EXISTS flight_records (
    flight_date DATE NOT NULL,
    year SMALLINT NOT NULL,
    month TINYINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day_of_week TINYINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    reporting_carrier VARCHAR NOT NULL,
    flight_number VARCHAR,
    origin CHAR(3) NOT NULL,
    destination CHAR(3) NOT NULL,
    crs_departure_minutes SMALLINT NOT NULL CHECK (crs_departure_minutes BETWEEN 0 AND 1439),
    crs_arrival_minutes SMALLINT NOT NULL CHECK (crs_arrival_minutes BETWEEN 0 AND 1439),
    departure_time_bucket VARCHAR NOT NULL,
    departure_delay_minutes DOUBLE,
    arrival_delay_minutes DOUBLE,
    cancelled BOOLEAN NOT NULL,
    diverted BOOLEAN NOT NULL,
    flight_status VARCHAR NOT NULL,
    carrier_delay_minutes DOUBLE,
    weather_delay_minutes DOUBLE,
    nas_delay_minutes DOUBLE,
    security_delay_minutes DOUBLE,
    late_aircraft_delay_minutes DOUBLE,
    arrival_delay_outlier BOOLEAN NOT NULL,
    source_file VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_flights (
    flight_date DATE NOT NULL,
    year SMALLINT NOT NULL,
    month TINYINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day_of_week TINYINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
    reporting_carrier VARCHAR NOT NULL,
    origin CHAR(3) NOT NULL,
    destination CHAR(3) NOT NULL,
    crs_departure_minutes SMALLINT NOT NULL CHECK (crs_departure_minutes BETWEEN 0 AND 1439),
    crs_arrival_minutes SMALLINT NOT NULL CHECK (crs_arrival_minutes BETWEEN 0 AND 1439),
    departure_time_bucket VARCHAR NOT NULL,
    arrival_delay_minutes DOUBLE NOT NULL,
    source_file VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS data_quality_monthly (
    source_file VARCHAR PRIMARY KEY,
    source_rows BIGINT NOT NULL,
    cleaned_rows BIGINT NOT NULL,
    completed_flights BIGINT NOT NULL,
    cancelled_flights BIGINT NOT NULL,
    diverted_flights BIGINT NOT NULL,
    missing_arrival_delay_rows BIGINT NOT NULL,
    invalid_field_rows BIGINT NOT NULL,
    inconsistent_status_rows BIGINT NOT NULL,
    arrival_delay_outlier_rows BIGINT NOT NULL
);

