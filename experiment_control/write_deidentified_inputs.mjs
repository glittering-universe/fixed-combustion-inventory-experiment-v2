import fs from "node:fs";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const columnName = (index) => {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    value = Math.floor((value - 1) / 26);
  }
  return name;
};

const payloadPath = path.resolve(process.argv[2]);
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
if (payload.schema_mode === "raw_structure" && payload.cover_sheet) {
  throw new Error("raw_structure payloads cannot add a cover sheet");
}
if (!Array.isArray(payload.values) || !Array.isArray(payload.values[0]) || payload.values[0].length === 0) {
  throw new Error("payload.values must contain a non-empty header row");
}
const columnCount = payload.values[0].length;
if (payload.values.some((row) => !Array.isArray(row) || row.length !== columnCount)) {
  throw new Error("all payload rows must preserve the source column count");
}
const workbook = Workbook.create();
if (payload.cover_sheet) {
  const cover = workbook.worksheets.add("输入说明");
  cover.getRange("A1:B4").values = [
    ["固定燃烧源实验输入", payload.source_tag || payload.tag],
    ["输入版本", payload.variant || "B0"],
    ["说明", "本工作表不参与核算；主数据表按名称读取。"],
    ["隐私", "企业标识已作稳定伪名化。"],
  ];
  cover.getRange("A1:B1").format = { fill: "#1F4E78", font: { bold: true, color: "#FFFFFF" } };
  cover.getRange("A:B").format.columnWidthPx = 260;
}
const sheet = workbook.worksheets.add(payload.sheet_name);
const values = payload.values;
if (values.length && values[0].length) {
  sheet.getRangeByIndexes(0, 0, values.length, values[0].length).values = values;
  const endCol = columnName(values[0].length - 1);
  sheet.freezePanes.freezeRows(1);
  sheet.getRangeByIndexes(0, 0, 1, values[0].length).format = {
    fill: "#1F4E78",
    font: { name: "微软雅黑", size: 9, bold: true, color: "#FFFFFF" },
    wrapText: true,
    rowHeightPx: 44,
  };
  if (values.length > 1) {
    sheet.getRangeByIndexes(1, 0, values.length - 1, values[0].length).format.font = {
      name: "微软雅黑",
      size: 9,
    };
  }
  sheet.getRange(`A:${endCol}`).format.columnWidthPx = 105;
}
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(payload.output_path);
process.stdout.write(JSON.stringify({
  output: payload.output_path,
  source_tag: payload.source_tag || payload.tag,
  schema_mode: payload.schema_mode || "legacy_selected_table",
  sheet: payload.sheet_name,
  rows: values.length - 1,
  columns: values[0]?.length ?? 0,
}));
