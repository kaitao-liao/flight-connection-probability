"use client";

import { KeyboardEvent, useId, useMemo, useState } from "react";
import carrierData from "../data/supported-carriers.json";

export type Carrier = { code: string; name: string };

export const supportedCarriers = carrierData as Carrier[];
export const supportedCarrierCodes = new Set(supportedCarriers.map((carrier) => carrier.code));

const MAX_RESULTS = 6;

export function carrierLabel(carrier: Carrier): string {
  return `${carrier.name} (${carrier.code})`;
}

function matchRank(carrier: Carrier, term: string): number {
  const code = carrier.code.toLocaleLowerCase();
  const name = carrier.name.toLocaleLowerCase();
  if (code === term) return 0;
  if (code.startsWith(term)) return 1;
  if (name.startsWith(term)) return 2;
  if (name.split(/\s+/).some((word) => word.startsWith(term))) return 3;
  return 4;
}

type Props = {
  id: string;
  label: string;
  value: string;
  error?: string;
  onChange: (code: string) => void;
};

export function CarrierCombobox({ id, label, value, error, onChange }: Props) {
  const listboxId = `${id}-options`;
  const errorId = `${id}-error`;
  const instanceId = useId();
  const selectedCarrier = supportedCarriers.find((carrier) => carrier.code === value);
  const [query, setQuery] = useState(selectedCarrier ? carrierLabel(selectedCarrier) : "");
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  const matches = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) return supportedCarriers.slice(0, MAX_RESULTS);
    return supportedCarriers
      .filter((carrier) =>
        carrier.code.toLocaleLowerCase().includes(term)
        || carrier.name.toLocaleLowerCase().includes(term))
      .sort((left, right) => matchRank(left, term) - matchRank(right, term)
        || left.name.localeCompare(right.name))
      .slice(0, MAX_RESULTS);
  }, [query]);

  function selectCarrier(carrier: Carrier) {
    onChange(carrier.code);
    setQuery(carrierLabel(carrier));
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
      selectCarrier(matches[activeIndex]);
    } else if (event.key === "Escape") {
      setOpen(false);
      setActiveIndex(-1);
    }
  }

  return (
    <div className="autocomplete-field">
      <label htmlFor={id}>{label}</label>
      <div className="autocomplete">
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
          placeholder="Search by airline name or code"
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
            className="autocomplete-clear"
            aria-label={`Clear ${label}`}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => { setQuery(""); onChange(""); setOpen(true); setActiveIndex(-1); }}
          >×</button>
        )}
        {open && (
          <ul id={listboxId} role="listbox" className="autocomplete-options">
            {matches.length ? matches.map((carrier, index) => (
              <li
                id={`${instanceId}-${index}`}
                key={carrier.code}
                role="option"
                aria-selected={carrier.code === value}
                className={index === activeIndex ? "active" : undefined}
                onMouseDown={(event) => { event.preventDefault(); selectCarrier(carrier); }}
              >
                <span>{carrier.name}</span>
                <strong>{carrier.code}</strong>
              </li>
            )) : <li className="autocomplete-no-results">No supported carriers found</li>}
          </ul>
        )}
      </div>
      {error && <span id={errorId} className="field-error">{error}</span>}
    </div>
  );
}
