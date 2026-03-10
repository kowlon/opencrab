import { useEffect, useMemo, useRef, useState } from "react";

export function SearchSelect({
  value,
  onChange,
  options,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hoverIdx, setHoverIdx] = useState(0);
  const [search, setSearch] = useState("");
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const justSelected = useRef(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hasOptions = options.length > 0;

  const displayValue = hasOptions ? (search || value) : value;

  const filtered = useMemo(() => {
    if (!hasOptions) return [];
    const q = search.trim().toLowerCase();
    const list = q ? options.filter((x) => x.toLowerCase().includes(q)) : options;
    return list.slice(0, 200);
  }, [options, search, hasOptions]);

  useEffect(() => {
    if (hoverIdx >= filtered.length) setHoverIdx(0);
  }, [filtered.length, hoverIdx]);

  return (
    <div ref={rootRef} style={{ position: "relative", flex: "1 1 auto", minWidth: 0 }}>
      <div style={{ position: "relative" }}>
        <input
          ref={inputRef}
          value={displayValue}
          onChange={(e) => {
            const v = e.target.value;
            if (hasOptions) {
              setSearch(v);
              setOpen(true);
            }
            onChange(v);
          }}
          placeholder={placeholder}
          onFocus={() => { if (hasOptions) setOpen(true); }}
          onBlur={() => {
            blurTimer.current = setTimeout(() => {
              blurTimer.current = null;
              setOpen(false);
              if (justSelected.current) {
                justSelected.current = false;
                setSearch("");
                return;
              }
              if (hasOptions && search) {
                setSearch("");
              }
            }, 150);
          }}
          onKeyDown={(e) => {
            if (!hasOptions) return;
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setHoverIdx((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setHoverIdx((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              if (open && filtered[hoverIdx]) {
                e.preventDefault();
                justSelected.current = true;
                onChange(filtered[hoverIdx]);
                setSearch("");
                setOpen(false);
              } else if (hasOptions && search.trim()) {
                e.preventDefault();
                justSelected.current = true;
                onChange(search.trim());
                setSearch("");
                setOpen(false);
              }
            } else if (e.key === "Escape") {
              setSearch("");
              setOpen(false);
            }
          }}
          disabled={disabled}
          style={{ paddingRight: hasOptions ? (value ? 72 : 44) : 12 }}
        />
        {hasOptions && (value || search) && !disabled && (
          <button
            type="button"
            className="btnSmall"
            onClick={() => {
              setSearch("");
              onChange("");
              setOpen(true);
              inputRef.current?.focus();
            }}
            style={{
              position: "absolute",
              right: 42,
              top: "50%",
              transform: "translateY(-50%)",
              width: 26,
              height: 26,
              padding: 0,
              borderRadius: 8,
              display: "grid",
              placeItems: "center",
              fontSize: 14,
              color: "var(--muted)",
              opacity: 0.7,
            }}
            title="清空"
          >
            ✕
          </button>
        )}
        {hasOptions && (
          <button
            type="button"
            className="btnSmall"
            onClick={() => {
              if (blurTimer.current) { clearTimeout(blurTimer.current); blurTimer.current = null; }
              if (!open) { setSearch(""); }
              setOpen((v) => !v);
              inputRef.current?.focus();
            }}
            disabled={disabled}
            style={{
              position: "absolute",
              right: 8,
              top: "50%",
              transform: "translateY(-50%)",
              width: 34,
              height: 30,
              padding: 0,
              borderRadius: 10,
              display: "grid",
              placeItems: "center",
            }}
          >
            ▾
          </button>
        )}
      </div>
      {open && hasOptions && !disabled ? (
        <div
          style={{
            position: "absolute",
            zIndex: 50,
            left: 0,
            right: 0,
            marginTop: 6,
            maxHeight: 280,
            overflow: "auto",
            border: "1px solid var(--line)",
            borderRadius: 14,
            background: "var(--panel2)",
            boxShadow: "0 18px 60px rgba(17, 24, 39, 0.14)",
          }}
          onMouseDown={(e) => {
            e.preventDefault();
          }}
        >
          {filtered.length === 0 ? (
            <div style={{ padding: 12, color: "var(--muted)", fontWeight: 650 }}>没有匹配项</div>
          ) : (
            filtered.map((opt, idx) => (
              <div
                key={opt}
                onMouseEnter={() => setHoverIdx(idx)}
                onClick={() => {
                  justSelected.current = true;
                  onChange(opt);
                  setSearch("");
                  setOpen(false);
                }}
                style={{
                  padding: "10px 12px",
                  cursor: "pointer",
                  fontWeight: 650,
                  background: opt === value
                    ? "rgba(14, 165, 233, 0.16)"
                    : idx === hoverIdx
                      ? "rgba(14, 165, 233, 0.06)"
                      : "transparent",
                  borderTop: idx === 0 ? "none" : "1px solid rgba(17,24,39,0.06)",
                }}
              >
                {opt === value ? `✓ ${opt}` : opt}
              </div>
            ))
          )}
        </div>
      ) : null}
    </div>
  );
}
