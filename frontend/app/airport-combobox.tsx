"use client";

import { KeyboardEvent, useId, useMemo, useState } from "react";
import airportData from "../data/supported-airports.json";

export type Airport = { code: string; city: string; name: string };

export const supportedAirports = airportData as Airport[];
export const supportedAirportCodes = new Set(supportedAirports.map((airport) => airport.code));

const MAX_RESULTS = 8;

export function airportLabel(airport: Airport): string {
  return `${airport.city} — ${airport.name} (${airport.code})`;
}

type Props = {
  id: string;
  label: string;
  value: string;
  error?: string;
  onChange: (code: string) => void;
};

export function AirportCombobox({ id, label, value, error, onChange }: Props) {
  const listboxId = `${id}-options`;
  const errorId = `${id}-error`;
  const instanceId = useId();
  const selectedAirport = supportedAirports.find((airport) => airport.code === value);
  const [query, setQuery] = useState(selectedAirport ? airportLabel(selectedAirport) : "");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const matches = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) return supportedAirports.slice(0, MAX_RESULTS);
    return supportedAirports
      .filter((airport) =>
        airport.code.toLocaleLowerCase().includes(term)
        || airport.city.toLocaleLowerCase().includes(term)
        || airport.name.toLocaleLowerCase().includes(term))
      .slice(0, MAX_RESULTS);
  }, [query]);

  function selectAirport(airport: Airport) {
    onChange(airport.code);
    setQuery(airportLabel(airport));
    setOpen(false);
    setActiveIndex(-1);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.min(current + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setOpen(true);
      setActiveIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter" && open && activeIndex >= 0) {
      event.preventDefault();
      selectAirport(matches[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
    }
  }

  return (
    <div className="airport-field">
      <label htmlFor={id}>{label}</label>
      <div className="airport-combobox">
        <input
          id={id}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={open}
          aria-controls={listboxId}
          aria-activedescendant={activeIndex >= 0 ? `${instanceId}-${activeIndex}` : undefined}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          value={query}
          placeholder="Search by city, airport, or IATA code"
          autoComplete="off"
          onFocus={() => setOpen(true)}
          onBlur={() => { setOpen(false); setActiveIndex(-1); }}
          onChange={(event) => {
            setQuery(event.target.value);
            onChange("");
            setOpen(true);
            setActiveIndex(-1);
          }}
          onKeyDown={handleKeyDown}
        />
        {query && (
          <button
            type="button"
            className="airport-clear"
            aria-label={`Clear ${label}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => { setQuery(""); onChange(""); setOpen(true); setActiveIndex(-1); }}
          >×</button>
        )}
        {open && (
          <ul id={listboxId} role="listbox" className="airport-options">
            {matches.length ? matches.map((airport, index) => (
              <li
                id={`${instanceId}-${index}`}
                key={airport.code}
                role="option"
                aria-selected={airport.code === value}
                className={index === activeIndex ? "active" : undefined}
                onMouseDown={(event) => { event.preventDefault(); selectAirport(airport); }}
              >
                <span>{airport.city} — {airport.name}</span>
                <strong>{airport.code}</strong>
              </li>
            )) : <li className="airport-no-results">No supported airports found</li>}
          </ul>
        )}
      </div>
      {error && <span id={errorId} className="field-error">{error}</span>}
    </div>
  );
}
