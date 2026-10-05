const PLAIN_INTEGER = /^[+-]?\d+$/;
const SINGLE_SEPARATOR_NUMBER = /^[+-]?\d+([.,])\d+$/;

export class TernaNumericParseError extends Error {
  constructor(message, rawValue) {
    super(`${message}: ${JSON.stringify(rawValue)}`);
    this.name = "TernaNumericParseError";
    this.rawValue = rawValue;
  }
}

export function detectTernaDecimalConventions(rawValues) {
  const evidence = {
    ".": { observed: 0, fractionalLengths: new Set() },
    ",": { observed: 0, fractionalLengths: new Set() },
  };
  const invalid = [];
  const bothSeparators = [];

  for (const original of rawValues) {
    if (typeof original === "number") {
      if (!Number.isFinite(original)) {
        throw new TernaNumericParseError("Typed numeric cell is not finite", original);
      }
      continue;
    }
    const raw = String(original ?? "").trim();
    if (PLAIN_INTEGER.test(raw)) continue;
    if (raw.includes(".") && raw.includes(",")) {
      bothSeparators.push(raw);
      continue;
    }
    const match = raw.match(SINGLE_SEPARATOR_NUMBER);
    if (!match) {
      invalid.push(raw);
      continue;
    }
    const separator = match[1];
    evidence[separator].observed += 1;
    evidence[separator].fractionalLengths.add(raw.split(separator)[1].length);
  }

  if (invalid.length > 0) {
    throw new TernaNumericParseError(
      "Invalid numeric syntax; punctuation was not interpreted",
      invalid[0],
    );
  }
  if (bothSeparators.length > 0) {
    throw new TernaNumericParseError(
      "Both comma and point occur in one value; thousands/decimal roles are ambiguous",
      bothSeparators[0],
    );
  }

  const decimalSeparators = new Set();
  const ambiguousSeparators = new Set();
  for (const separator of [".", ","]) {
    const item = evidence[separator];
    if (item.observed === 0) continue;
    const lengths = [...item.fractionalLengths];
    if (lengths.some((length) => length !== 3)) {
      decimalSeparators.add(separator);
    } else {
      ambiguousSeparators.add(separator);
    }
  }

  if (ambiguousSeparators.size > 0) {
    throw new TernaNumericParseError(
      "Separator is used only in three-digit groups; decimal versus thousands meaning is ambiguous",
      [...ambiguousSeparators].join("|"),
    );
  }

  return {
    decimalSeparators,
    evidence: Object.fromEntries(
      Object.entries(evidence).map(([separator, item]) => [
        separator,
        {
          observed: item.observed,
          fractional_lengths: [...item.fractionalLengths].sort((a, b) => a - b),
        },
      ]),
    ),
    detection_rule:
      "A punctuation mark is accepted as a decimal separator only when the same payload contains at least one value using that mark with a non-three-digit fractional part. Mixed punctuation within one value and separator-only three-digit groups are rejected.",
  };
}

export function parseTernaNumber(original, convention) {
  if (typeof original === "number") {
    if (!Number.isFinite(original)) {
      throw new TernaNumericParseError("Typed numeric cell is not finite", original);
    }
    return {
      value: original,
      raw_value: String(original),
      raw_cell_type: "NUMBER",
      decimal_separator: null,
      parser_status: "TYPED_NUMERIC_CELL",
    };
  }
  const rawValue = String(original ?? "");
  const raw = rawValue.trim();
  if (PLAIN_INTEGER.test(raw)) {
    const value = Number(raw);
    if (!Number.isSafeInteger(value)) {
      throw new TernaNumericParseError("Integer is outside the safe range", rawValue);
    }
    return {
      value,
      raw_value: rawValue,
      raw_cell_type: "STRING",
      decimal_separator: null,
      parser_status: "PARSED_INTEGER",
    };
  }
  if (raw.includes(".") && raw.includes(",")) {
    throw new TernaNumericParseError(
      "Both comma and point occur in one value; value rejected",
      rawValue,
    );
  }
  const match = raw.match(SINGLE_SEPARATOR_NUMBER);
  if (!match) {
    throw new TernaNumericParseError("Invalid numeric syntax", rawValue);
  }
  const separator = match[1];
  if (!convention.decimalSeparators.has(separator)) {
    throw new TernaNumericParseError(
      "Separator convention is not unambiguously decimal in this payload",
      rawValue,
    );
  }
  const normalized = raw.replace(separator, ".");
  const value = Number(normalized);
  if (!Number.isFinite(value)) {
    throw new TernaNumericParseError("Parsed value is not finite", rawValue);
  }
  return {
    value,
    raw_value: rawValue,
    raw_cell_type: "STRING",
    decimal_separator: separator,
    parser_status: "PARSED_DECIMAL",
  };
}
