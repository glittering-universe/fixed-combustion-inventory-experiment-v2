import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [payloadPath, outputPath, previewDir] = process.argv.slice(2);
if (!payloadPath || !outputPath || !previewDir) throw new Error("usage: build_compact_workbooks.mjs payload.json output.xlsx preview-dir");
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();
const progress = (message) => process.stderr.write(`[workbook] ${message}\n`);
const colors = {
  navy: "#183B56", teal: "#1F6D70", green: "#E2F0D9", yellow: "#FFF2CC",
  blue: "#DDEBF7", gray: "#F3F5F7", border: "#D6DEE3", white: "#FFFFFF",
  red: "#FCE4D6", text: "#243746",
};

const colName = (index) => {
  let n = index + 1;
  let out = "";
  while (n) {
    n -= 1;
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26);
  }
  return out;
};
const colIndex = (headers, name) => {
  const index = headers.indexOf(name);
  if (index < 0) throw new Error(`missing column: ${name}`);
  return index;
};
const r1c1 = (headers, name) => `RC${colIndex(headers, name) + 1}`;
const writeBlock = (sheet, row, column, matrix) => {
  if (matrix.length && matrix[0]?.length) sheet.getRangeByIndexes(row, column, matrix.length, matrix[0].length).values = matrix;
};
const baseStyle = (sheet, headers, rowCount, freezeCols = 1) => {
  const end = colName(headers.length - 1);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  if (freezeCols) sheet.freezePanes.freezeColumns(freezeCols);
  sheet.getRange(`A1:${end}1`).format = {
    fill: colors.teal, font: { name: "微软雅黑", size: 9, bold: true, color: colors.white },
    horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, rowHeightPx: 48,
    borders: { preset: "outside", style: "thin", color: colors.border },
  };
  if (rowCount) sheet.getRange(`A2:${end}${rowCount + 1}`).format.font = { name: "微软雅黑", size: 9, color: colors.text };
};
const widths = (sheet, specs) => specs.forEach(([range, width]) => { sheet.getRange(range).format.columnWidthPx = width; });
const statusFormat = (range) => {
  range.conditionalFormats.add("containsText", { text: "信息不足", format: { fill: colors.red, font: { color: "#9C0006" } } });
  range.conditionalFormats.add("containsText", { text: "withhold", format: { fill: colors.red, font: { color: "#9C0006" } } });
  range.conditionalFormats.add("containsText", { text: "缺失", format: { fill: colors.yellow, font: { color: "#7F6000" } } });
  range.conditionalFormats.add("containsText", { text: "已计算", format: { fill: colors.green, font: { color: "#006100" } } });
};
const addSheet = (name, headers, rows, freezeCols = 1, specs = []) => {
  const sheet = workbook.worksheets.add(name);
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, rows);
  baseStyle(sheet, headers, rows.length, freezeCols);
  if (specs.length) widths(sheet, specs);
  return sheet;
};

// 00 说明
{
  const sheet = workbook.worksheets.add("00_说明");
  sheet.showGridLines = false;
  sheet.mergeCells("A1:F2");
  sheet.getRange("A1").values = [[payload.title]];
  sheet.getRange("A1:F2").format = {
    fill: colors.navy, font: { name: "微软雅黑", size: 18, bold: true, color: colors.white },
    verticalAlignment: "center", horizontalAlignment: "left",
  };
  const rows = [
    ["项目", "内容"], ["统计年份", "2022"], ["源类", payload.target_label], ["源记录数", payload.sources.length],
    ["燃料计算行", payload.calculations.length], ["污染物排放核算项", payload.calculation_item_count],
    ["核算方法", "排放因子法；燃煤SO₂和颗粒物采用物料衡算；未建立CEMS方法路径。"],
    ["基102填报值", "只在05_源结果中用于对比，不参与方法选择、效率反推或最终排放计算。"],
    ["治理口径", "设施投运率经验设为1；采用T/CSES表A.1工艺缺省效率，不代表真实设施去除效率。"],
    ["缺失处置", "仅治理信息缺失可经验按0；燃料、活动水平、硫分、灰分、容量或因子缺失时结果留空。"],
    ["PM₁₀", "细粒与粗粒分别应用PM₂.₅和PM₂.₅–₁₀效率后相加。"],
    ["NH₃", "只作为SCR/SNCR氨逃逸独立核算；SCR=0.16、SNCR=0.17 g/kg煤。"],
    ["规则包", `${payload.rule_package_id} v${payload.rule_package_version}`], ["规则包哈希", payload.rule_package_hash],
    ["源级汇总", "05_源结果为03/04公式计算结果的已复核汇总快照；全部核算公式保留在03_燃料污染物计算与04_氨逃逸。"],
    ["颜色", "黄色=输入/填报对比；绿色=Excel公式；蓝色=已复核规则参数或汇总快照；红色=需补充信息。"],
  ];
  writeBlock(sheet, 3, 0, rows);
  sheet.getRange("A4:B4").format = { fill: colors.teal, font: { bold: true, color: colors.white } };
  sheet.getRange(`A5:B${rows.length + 3}`).format = { wrapText: true, verticalAlignment: "top" };
  widths(sheet, [["A:A", 155], ["B:B", 720], ["C:F", 30]]);
}
progress("说明页完成");

// 01 源清单
{
  const sheet = addSheet("01_源清单", payload.source_headers, payload.sources, 5,
    [["A:A", 180], ["B:B", 90], ["C:C", 320], ["D:I", 125], ["J:Q", 145], ["R:Z", 145]]);
  const end = payload.sources.length + 1;
  if (payload.sources.length) {
    sheet.getRange(`A2:Q${end}`).format.fill = colors.gray;
    sheet.getRange(`R2:Z${end}`).format.fill = colors.yellow;
    sheet.getRange(`H2:I${end}`).format.numberFormat = "0.000000";
  }
}
progress("源清单完成");

// 02 燃料明细（原始值与匹配快照）
{
  const sheet = addSheet("02_燃料明细", payload.fuel_headers, payload.fuels, 2,
    [["A:B", 190], ["C:D", 120], ["E:H", 210], ["I:Q", 125], ["R:R", 150], ["S:T", 300], ["U:U", 80]]);
  const end = payload.fuels.length + 1;
  if (payload.fuels.length) {
    sheet.getRange(`E2:M${end}`).format.fill = colors.yellow;
    sheet.getRange(`F2:H${end}`).format.fill = colors.blue;
    sheet.getRange(`P2:P${end}`).format.fill = colors.blue;
    statusFormat(sheet.getRange(`R2:R${end}`));
  }
}
progress("燃料明细完成");

// Parameter sheets precede formula sheets.
const parameterSpecs = [
  ["P_排放因子", payload.factor_headers, payload.factors, [["A:A", 410], ["B:B", 230], ["C:E", 120], ["F:H", 180]]],
  ["P_燃煤参数", payload.coal_headers, payload.coal_parameters, [["A:A", 320], ["B:B", 170], ["C:H", 115], ["I:J", 180]]],
  ["P_治理效率", payload.control_efficiency_headers, payload.control_efficiencies, [["A:B", 230], ["C:D", 120], ["E:G", 180]]],
  ["P_燃料映射", payload.fuel_mapping_headers, payload.fuel_mappings, [["A:B", 275], ["C:E", 135], ["F:F", 380]]],
  ["P_燃烧映射", payload.combustion_mapping_headers, payload.combustion_mappings, [["A:B", 275], ["C:E", 150], ["F:F", 380]]],
  ["P_治理映射", payload.control_mapping_headers, payload.control_mappings, [["A:C", 290], ["D:G", 130], ["H:H", 430]]],
  ["P_规则索引", payload.rule_headers, payload.rules, [["A:A", 245], ["B:C", 155], ["D:D", 245], ["E:F", 510], ["G:G", 145]]],
];
for (const [name, headers, rows, specs] of parameterSpecs) addSheet(name, headers, rows, 1, specs);
progress("参数表完成");

// 03 每个燃料一行、八种燃烧污染物并列；每行仅三条横向公式。
{
  const headers = payload.calculation_headers;
  const rows = payload.calculations;
  const sheet = addSheet("03_燃料污染物计算", headers, rows, 4);
  const n = rows.length;
  const factorLast = payload.factors.length + 1;
  if (n) {
    const activityStart = colIndex(headers, "标准活动水平(公式)");
    const factorStart = colIndex(headers, "SO2_有效因子(公式)");
    const generationStart = colIndex(headers, "SO2_产生量(公式,t)");
    const emissionStart = colIndex(headers, "SO2_排放量(公式,t)");
    const formulaRows = Array.from({ length: n }, () => [
      `=HSTACK(IF(${r1c1(headers,"原始单位")}="吨",${r1c1(headers,"原始消耗量")}*1000,IF(${r1c1(headers,"原始单位")}="万立方米",${r1c1(headers,"原始消耗量")}*10000,"")),IF(${r1c1(headers,"原始单位")}="万立方米","m3",IF(${r1c1(headers,"原始单位")}="吨","kg","")))`,
    ]);
    sheet.getRange(`${colName(activityStart)}2:${colName(activityStart)}${n + 1}`).formulasR1C1 = formulaRows;

    const factorExpressions = [];
    const generationExpressions = [];
    const emissionExpressions = [];
    payload.pollutants.slice(0, 8).forEach((pollutant, index) => {
      const key = r1c1(headers, `${pollutant}_因子键`);
      const mode = r1c1(headers, `${pollutant}_因子模式`);
      const raw = r1c1(headers, `${pollutant}_原始参数值`);
      const action = r1c1(headers, `${pollutant}_准入动作`);
      const control = r1c1(headers, `${pollutant}_一般去除效率(%)`);
      const fine = r1c1(headers, `${pollutant}_PM2.5去除效率(%)`);
      const coarse = r1c1(headers, `${pollutant}_PM2.5-10去除效率(%)`);
      const matched = `COUNTIF('P_排放因子'!R2C1:R${factorLast}C1,${key})=1`;
      const particleMissing = pollutant === "PM25"
        ? `OR(NOT(ISNUMBER(${r1c1(headers,"灰分(%)")})),NOT(ISNUMBER(${r1c1(headers,"灰入底灰比")})),NOT(ISNUMBER(${r1c1(headers,"PM2.5比例")})))`
        : pollutant === "PM10"
          ? `OR(NOT(ISNUMBER(${r1c1(headers,"灰分(%)")})),NOT(ISNUMBER(${r1c1(headers,"灰入底灰比")})),NOT(ISNUMBER(${r1c1(headers,"PM10比例")})))`
          : pollutant === "BC"
            ? `OR(NOT(ISNUMBER(${r1c1(headers,"灰分(%)")})),NOT(ISNUMBER(${r1c1(headers,"灰入底灰比")})),NOT(ISNUMBER(${r1c1(headers,"PM2.5比例")})),NOT(ISNUMBER(${r1c1(headers,"BC占PM2.5")})))`
            : pollutant === "OC"
              ? `OR(NOT(ISNUMBER(${r1c1(headers,"灰分(%)")})),NOT(ISNUMBER(${r1c1(headers,"灰入底灰比")})),NOT(ISNUMBER(${r1c1(headers,"PM2.5比例")})),NOT(ISNUMBER(${r1c1(headers,"OC占PM2.5")})))`
              : "TRUE";
      factorExpressions.push(
        `IF(${matched},IF(OR(${mode}="constant",${mode}="constant_sum"),${raw},IF(${mode}="capacity_lookup",IF(NOT(ISNUMBER(${r1c1(headers,"装机容量(MW)")})),"",IF(${r1c1(headers,"装机容量(MW)")}<=100,8.96,IF(${r1c1(headers,"装机容量(MW)")}<300,8.19,7.21))),IF(${mode}="coal_sulfur_balance",IF(OR(NOT(ISNUMBER(${r1c1(headers,"含硫量")})),${r1c1(headers,"含硫量单位")}<>"%",NOT(ISNUMBER(${r1c1(headers,"硫入底灰比")}))),"",20*${r1c1(headers,"含硫量")}*(1-${r1c1(headers,"硫入底灰比")})),IF(${mode}="coal_particle_balance",IF(${particleMissing},"",${pollutant === "PM25" ? `10*${r1c1(headers,"灰分(%)")}*(1-${r1c1(headers,"灰入底灰比")})*${r1c1(headers,"PM2.5比例")}` : pollutant === "PM10" ? `10*${r1c1(headers,"灰分(%)")}*(1-${r1c1(headers,"灰入底灰比")})*${r1c1(headers,"PM10比例")}` : pollutant === "BC" ? `10*${r1c1(headers,"灰分(%)")}*(1-${r1c1(headers,"灰入底灰比")})*${r1c1(headers,"PM2.5比例")}*${r1c1(headers,"BC占PM2.5")}` : pollutant === "OC" ? `10*${r1c1(headers,"灰分(%)")}*(1-${r1c1(headers,"灰入底灰比")})*${r1c1(headers,"PM2.5比例")}*${r1c1(headers,"OC占PM2.5")}` : '""'}),"")))),"")`,
      );
      const factorFormulaCell = `RC${factorStart + index + 1}`;
      const generationFormulaCell = `RC${generationStart + index + 1}`;
      generationExpressions.push(`IF(${action}="allow_calculation",IF(AND(ISNUMBER(RC${activityStart + 1}),ISNUMBER(${factorFormulaCell})),RC${activityStart + 1}*${factorFormulaCell}/1000000,""),IF(${action}="not_applicable",0,""))`);
      if (pollutant === "PM10") {
        const fineGeneration = `RC${generationStart + 5 + 1}`;
        emissionExpressions.push(`IF(${action}="allow_calculation",IF(AND(ISNUMBER(${generationFormulaCell}),ISNUMBER(${fineGeneration})),${fineGeneration}*(1-${fine}/100)+(${generationFormulaCell}-${fineGeneration})*(1-${coarse}/100),""),IF(${action}="not_applicable",0,""))`);
      } else {
        emissionExpressions.push(`IF(${action}="allow_calculation",IF(ISNUMBER(${generationFormulaCell}),${generationFormulaCell}*(1-${control}/100),""),IF(${action}="not_applicable",0,""))`);
      }
    });
    sheet.getRange(`${colName(factorStart)}2:${colName(factorStart)}${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [`=HSTACK(${factorExpressions.join(",")})`]);
    sheet.getRange(`${colName(generationStart)}2:${colName(generationStart)}${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [`=HSTACK(${generationExpressions.join(",")})`]);
    sheet.getRange(`${colName(emissionStart)}2:${colName(emissionStart)}${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [`=HSTACK(${emissionExpressions.join(",")})`]);
    const end = n + 1;
    sheet.getRange(`${colName(activityStart)}2:${colName(activityStart + 1)}${end}`).format.fill = colors.green;
    sheet.getRange(`${colName(28)}2:${colName(factorStart - 1)}${end}`).format.fill = colors.blue;
    sheet.getRange(`${colName(factorStart)}2:${colName(headers.length - 1)}${end}`).format.fill = colors.green;
    sheet.getRange(`${colName(activityStart)}2:${colName(headers.length - 1)}${end}`).format.numberFormat = "0.000000";
    for (const pollutant of payload.pollutants.slice(0, 8)) statusFormat(sheet.getRange(`${colName(colIndex(headers, `${pollutant}_准入动作`))}2:${colName(colIndex(headers, `${pollutant}_准入动作`))}${end}`));
  }
  baseStyle(sheet, headers, n, 4);
  widths(sheet, [["A:B", 190], ["C:D", 115], ["E:E", 310], ["F:N", 125], ["O:Z", 115], [`AA:${colName(headers.length - 1)}`, 118]]);
}
progress("燃料污染物公式表完成");

// 04 NH3 is a separate SCR/SNCR ammonia-slip calculation.
{
  const headers = payload.nh3_headers;
  const rows = payload.nh3;
  const sheet = addSheet("04_氨逃逸", headers, rows, 2,
    [["A:A", 190], ["B:B", 310], ["C:J", 160], ["K:Q", 135]]);
  const n = rows.length;
  const calcLast = payload.calculations.length + 1;
  const factorLast = payload.factors.length + 1;
  const calcHeaders = payload.calculation_headers;
  if (n) {
    const sourceCol = colIndex(calcHeaders, "源ID") + 1;
    const fuelCol = colIndex(calcHeaders, "标准燃料") + 1;
    const activityCol = colIndex(calcHeaders, "标准活动水平(公式)") + 1;
    const activityFormula = `=IF(RC8="withhold","",IF(RC8="not_applicable",0,IF(RC10="治理信息缺失按0",0,SUMIFS('03_燃料污染物计算'!R2C${activityCol}:R${calcLast}C${activityCol},'03_燃料污染物计算'!R2C${sourceCol}:R${calcLast}C${sourceCol},RC1,'03_燃料污染物计算'!R2C${fuelCol}:R${calcLast}C${fuelCol},"煤炭")+SUMIFS('03_燃料污染物计算'!R2C${activityCol}:R${calcLast}C${activityCol},'03_燃料污染物计算'!R2C${sourceCol}:R${calcLast}C${sourceCol},RC1,'03_燃料污染物计算'!R2C${fuelCol}:R${calcLast}C${fuelCol},"煤矸石"))))`;
    const factorFormula = `=IFERROR(VLOOKUP(RC5,'P_排放因子'!R2C1:R${factorLast}C8,4,FALSE),"")`;
    const generationFormula = `=IF(RC8="withhold","",IF(RC8="not_applicable",0,RC14*RC15/1000000))`;
    const emissionFormula = `=IF(RC8="withhold","",IF(RC8="not_applicable",0,RC16))`;
    sheet.getRange(`N2:N${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [activityFormula]);
    sheet.getRange(`O2:O${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [factorFormula]);
    sheet.getRange(`P2:P${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [generationFormula]);
    sheet.getRange(`Q2:Q${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [emissionFormula]);
    sheet.getRange(`E2:M${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`N2:Q${n + 1}`).format.fill = colors.green;
    sheet.getRange(`G2:Q${n + 1}`).format.numberFormat = "0.000000";
    statusFormat(sheet.getRange(`H2:J${n + 1}`));
  }
}
progress("NH3公式表完成");

// 05 source results aggregate only the local fuel rows belonging to each source.
{
  const headers = payload.result_headers;
  const rows = payload.results;
  const sheet = addSheet("05_源结果", headers, rows, 3,
    [["A:A", 190], ["B:B", 310], ["C:G", 125], ["H:I", 110], ["J:BA", 135], ["BB:BC", 100]]);
  const n = rows.length;
  if (n) {
    payload.pollutants.forEach((pollutant, pollutantIndex) => {
      const groupStart = 10 + pollutantIndex * 4;
      sheet.getRange(`${colName(groupStart - 1)}2:${colName(groupStart)}${n + 1}`).format.fill = colors.blue;
      sheet.getRange(`${colName(groupStart + 1)}2:${colName(groupStart + 1)}${n + 1}`).format.fill = colors.blue;
      sheet.getRange(`${colName(groupStart + 2)}2:${colName(groupStart + 2)}${n + 1}`).format.fill = colors.green;
      statusFormat(sheet.getRange(`${colName(groupStart + 2)}2:${colName(groupStart + 2)}${n + 1}`));
    });
    sheet.getRange(`AT2:BA${n + 1}`).format.fill = colors.yellow;
    sheet.getRange(`BB2:BC${n + 1}`).format.fill = colors.gray;
    sheet.getRange(`H2:I${n + 1}`).format.numberFormat = "0.000000";
    sheet.getRange(`J2:BA${n + 1}`).format.numberFormat = "0.000000";
  }
}
progress("源结果完成");

addSheet("06_异常清单", payload.exception_headers, payload.exceptions, 2,
  [["A:A", 190], ["B:B", 310], ["C:H", 155], ["I:J", 370]]);
addSheet("07_转移排除", payload.ledger_headers, payload.ledger, 4,
  [["A:D", 155], ["E:E", 310], ["F:J", 155], ["K:K", 430]]);
addSheet("08_逐步复核", payload.review_headers, payload.review, 1,
  [["A:A", 70], ["B:B", 225], ["C:C", 530], ["D:F", 140], ["G:H", 280]]);
progress("异常、分流和复核表完成");

const formulaErrors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 300 },
  summary: "formula error scan",
});
const calculationCheck = await workbook.inspect({
  kind: "table", range: `03_燃料污染物计算!A1:${colName(payload.calculation_headers.length - 1)}8`, include: "values,formulas",
  tableMaxRows: 8, tableMaxCols: payload.calculation_headers.length, maxChars: 60000,
});
const resultCheck = await workbook.inspect({
  kind: "table", range: "05_源结果!A1:BC8", include: "values,formulas", tableMaxRows: 8, tableMaxCols: 55, maxChars: 30000,
});
progress("公式检查完成");

await fs.mkdir(previewDir, { recursive: true });
const calcEnd = colName(payload.calculation_headers.length - 1);
const previewSpecs = [
  ["00_说明", "A1:F19"], ["01_源清单", "A1:Z12"], ["02_燃料明细", "A1:U12"],
  ["03_燃料污染物计算", "A1:AB12"], ["03_燃料污染物计算", `AC1:BP12`], ["03_燃料污染物计算", `BQ1:${calcEnd}12`],
  ["04_氨逃逸", "A1:Q12"], ["05_源结果", "A1:Q12"], ["05_源结果", "R1:BC12"],
  ["06_异常清单", "A1:J16"], ["07_转移排除", "A1:K16"], ["08_逐步复核", "A1:H14"],
  ["P_排放因子", "A1:H12"], ["P_燃煤参数", "A1:J12"], ["P_治理效率", "A1:G12"],
  ["P_燃料映射", "A1:F12"], ["P_燃烧映射", "A1:F12"], ["P_治理映射", "A1:H12"], ["P_规则索引", "A1:G12"],
];
const previews = [];
for (let index = 0; index < previewSpecs.length; index += 1) {
  const [sheetName, range] = previewSpecs[index];
  const blob = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  const filename = `${String(index + 1).padStart(2, "0")}_${sheetName.replace(/[^0-9A-Za-z\u4e00-\u9fff]/g, "_")}.png`;
  await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await blob.arrayBuffer()));
  previews.push({ sheet: sheetName, range, filename });
}
progress("逐表预览完成");

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
progress("XLSX导出完成");
const qa = {
  output_path: outputPath, formula_error_scan: formulaErrors.ndjson,
  calculation_check: calculationCheck.ndjson, result_check: resultCheck.ndjson,
  previews, sheet_count: 16,
};
await fs.writeFile(path.join(previewDir, "qa.json"), JSON.stringify(qa, null, 2), "utf8");
console.log(JSON.stringify({ success: true, output_path: outputPath, preview_dir: previewDir, sheet_count: 16 }));
