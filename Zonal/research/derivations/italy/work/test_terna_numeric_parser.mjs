import assert from "node:assert/strict";
import {
  TernaNumericParseError,
  detectTernaDecimalConventions,
  parseTernaNumber,
} from "../scripts/terna_numeric_parser.mjs";

const dot = detectTernaDecimalConventions([1234.5, "0.01", "1.2", "3.456", "1237"]);
assert.deepEqual(parseTernaNumber(1234.5, dot), {
  value: 1234.5,
  raw_value: "1234.5",
  raw_cell_type: "NUMBER",
  decimal_separator: null,
  parser_status: "TYPED_NUMERIC_CELL",
});
assert.equal(parseTernaNumber("0.002", dot).value, 0.002);
assert.equal(parseTernaNumber("1237", dot).value, 1237);

const comma = detectTernaDecimalConventions(["0,01", "1,2", "3,456", "42"]);
assert.equal(parseTernaNumber("0,002", comma).value, 0.002);

assert.throws(
  () => detectTernaDecimalConventions(["1,234", "2,345"]),
  TernaNumericParseError,
);
assert.throws(
  () => detectTernaDecimalConventions(["1,234.5", "2.5"]),
  TernaNumericParseError,
);
assert.throws(
  () => detectTernaDecimalConventions(["1,234,567", "2,4"]),
  TernaNumericParseError,
);

console.log(JSON.stringify({ ok: true, tests: 8 }));
