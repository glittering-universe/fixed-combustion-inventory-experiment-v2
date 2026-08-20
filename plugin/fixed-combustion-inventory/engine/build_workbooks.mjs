import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [payloadPath, outputPath, previewDir] = process.argv.slice(2);
if (!payloadPath || !outputPath || !previewDir) {
  throw new Error("usage: build_workbooks.mjs payload.json output.xlsx preview-dir");
}
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
const workbook = Workbook.create();
const progress = (message) => process.stderr.write(`[workbook] ${message}\n`);
const colors = {
  navy: "#183B56", teal: "#1F6D70", green: "#E2F0D9", yellow: "#FFF2CC",
  blue: "#DDEBF7", gray: "#F3F5F7", border: "#D6DEE3", white: "#FFFFFF",
  red: "#FCE4D6", orange: "#FCE4D6", text: "#243746",
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

const writeBlock = (sheet, startRow, startCol, matrix) => {
  if (!matrix.length || !matrix[0]?.length) return;
  sheet.getRangeByIndexes(startRow, startCol, matrix.length, matrix[0].length).values = matrix;
};

const baseStyle = (sheet, headers, rowCount, freezeCols = 1) => {
  const end = colName(headers.length - 1);
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  if (freezeCols) sheet.freezePanes.freezeColumns(freezeCols);
  sheet.getRange(`A1:${end}1`).format = {
    fill: colors.teal,
    font: { name: "微软雅黑", size: 9, bold: true, color: colors.white },
    verticalAlignment: "center",
    horizontalAlignment: "center",
    wrapText: true,
    rowHeightPx: 46,
    borders: { preset: "outside", style: "thin", color: colors.border },
  };
  if (rowCount > 0) {
    sheet.getRange(`A2:${end}${rowCount + 1}`).format.font = { name: "微软雅黑", size: 9, color: colors.text };
  }
};

const setWidths = (sheet, specs) => {
  for (const [range, width] of specs) sheet.getRange(range).format.columnWidthPx = width;
};

const addStatusFormatting = (range) => {
  range.conditionalFormats.add("containsText", { text: "信息不足", format: { fill: colors.red, font: { color: "#9C0006" } } });
  range.conditionalFormats.add("containsText", { text: "withhold", format: { fill: colors.red, font: { color: "#9C0006" } } });
  range.conditionalFormats.add("containsText", { text: "缺失", format: { fill: colors.yellow, font: { color: "#7F6000" } } });
  range.conditionalFormats.add("containsText", { text: "无法映射", format: { fill: colors.orange, font: { color: "#9C6500" } } });
  range.conditionalFormats.add("containsText", { text: "已计算", format: { fill: colors.green, font: { color: "#006100" } } });
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
    ["项目", "内容"],
    ["统计年份", "2022"],
    ["源类", payload.target_label],
    ["源记录数", payload.sources.length],
    ["燃料明细数", payload.fuels.length],
    ["污染物核算项数", payload.items.length],
    ["方法范围", "排放因子法；燃煤SO₂/颗粒物等采用物料衡算。未建立CEMS路径。"],
    ["基102填报值", "仅在04_源结果中作对比，不参与方法选择、效率反推或最终排放计算。"],
    ["治理效率", "设施投运率经验假设为1；使用T/CSES表A.1工艺缺省平均效率，不代表设施真实去除效率。"],
    ["缺失处置", "治理信息缺失可经验按0；燃料、活动水平、燃煤参数、容量或因子缺失时结果留空。"],
    ["PM₁₀", "细粒与粗粒分别应用PM₂.₅和PM₂.₅–₁₀效率后相加。"],
    ["NH₃", "只作为SCR/SNCR氨逃逸独立核算；SCR=0.16、SNCR=0.17 g/kg煤。"],
    ["规则包", `${payload.rule_package_id} v${payload.rule_package_version}`],
    ["规则包哈希", payload.rule_package_hash],
    ["颜色", "黄色=原始输入/对比值；绿色=Excel公式；蓝色=规则匹配或参数快照；红色=需复核。"],
  ];
  writeBlock(sheet, 3, 0, rows);
  sheet.getRange("A4:B4").format = { fill: colors.teal, font: { bold: true, color: colors.white } };
  sheet.getRange(`A5:B${rows.length + 3}`).format = { wrapText: true, verticalAlignment: "top" };
  setWidths(sheet, [["A:A", 150], ["B:B", 700], ["C:F", 30]]);
}
progress("说明页完成");

// 01 源清单
{
  const sheet = workbook.worksheets.add("01_源清单");
  const headers = payload.source_headers;
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, payload.sources);
  baseStyle(sheet, headers, payload.sources.length, 5);
  setWidths(sheet, [["A:A", 170], ["B:B", 80], ["C:C", 310], ["D:I", 120], ["J:Q", 130], ["R:Z", 150]]);
  const end = payload.sources.length + 1;
  if (payload.sources.length) {
    sheet.getRange(`A2:Q${end}`).format.fill = colors.gray;
    sheet.getRange(`R2:Z${end}`).format.fill = colors.yellow;
    sheet.getRange(`H2:I${end}`).format.numberFormat = "0.000000";
  }
}
progress("源清单完成");

// 02 燃料明细：活动水平必须由Excel公式计算。
{
  const sheet = workbook.worksheets.add("02_燃料明细");
  const headers = payload.fuel_headers;
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, payload.fuels);
  const n = payload.fuels.length;
  if (n) {
    const end = n + 1;
    sheet.getRange("N2").formulas = [[`=IF(J2:J${end}="吨",I2:I${end}*1000,IF(J2:J${end}="万立方米",I2:I${end}*10000,""))`]];
    sheet.getRange("O2").formulas = [[`=IF(N2:N${end}="","",IF(J2:J${end}="万立方米","m3","kg"))`]];
    sheet.getRange("Q2").formulas = [[`=IF(ISNUMBER(N2:N${end})*ISNUMBER(P2:P${end}),N2:N${end}-P2:P${end},"")`]];
    sheet.getRange(`I2:M${n + 1}`).format.fill = colors.yellow;
    sheet.getRange(`F2:H${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`N2:O${n + 1}`).format.fill = colors.green;
    sheet.getRange(`Q2:Q${n + 1}`).format.fill = colors.green;
    sheet.getRange(`I2:Q${n + 1}`).format.numberFormat = "0.000000";
    addStatusFormatting(sheet.getRange(`R2:R${n + 1}`));
  }
  baseStyle(sheet, headers, n, 2);
  setWidths(sheet, [["A:B", 180], ["C:D", 120], ["E:H", 210], ["I:Q", 120], ["R:R", 155], ["S:T", 300], ["U:U", 80]]);
}
progress("燃料明细完成");

// Parameter sheets are created before formula sheets that reference them.
let parameterTableCounter = 0;
const addParameterSheet = (name, headers, rows, widths = []) => {
  const sheet = workbook.worksheets.add(name);
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, rows);
  baseStyle(sheet, headers, rows.length, 1);
  if (widths.length) setWidths(sheet, widths);
  else setWidths(sheet, [[`A:${colName(headers.length - 1)}`, 135]]);
  if (rows.length && rows.length < 5000) {
    parameterTableCounter += 1;
    const table = sheet.tables.add(
      `A1:${colName(headers.length - 1)}${rows.length + 1}`,
      true,
      `ParamTable${parameterTableCounter}${payload.target}`,
    );
    table.style = "TableStyleMedium2";
  }
  return sheet;
};

addParameterSheet("P_排放因子", payload.factor_headers, payload.factors,
  [["A:A", 390], ["B:B", 210], ["C:E", 120], ["F:H", 180]]);
addParameterSheet("P_燃煤参数", payload.coal_headers, payload.coal_parameters,
  [["A:A", 300], ["B:B", 160], ["C:H", 115], ["I:J", 180]]);
addParameterSheet("P_治理效率", payload.control_efficiency_headers, payload.control_efficiencies,
  [["A:B", 220], ["C:D", 120], ["E:G", 180]]);
addParameterSheet("P_燃料映射", payload.fuel_mapping_headers, payload.fuel_mappings,
  [["A:B", 270], ["C:E", 135], ["F:F", 360]]);
addParameterSheet("P_燃烧映射", payload.combustion_mapping_headers, payload.combustion_mappings,
  [["A:B", 270], ["C:E", 150], ["F:F", 360]]);
addParameterSheet("P_治理映射", payload.control_mapping_headers, payload.control_mappings,
  [["A:C", 285], ["D:G", 130], ["H:H", 420]]);
addParameterSheet("P_规则索引", payload.rule_headers, payload.rules,
  [["A:A", 240], ["B:C", 150], ["D:D", 240], ["E:F", 500], ["G:G", 140]]);
progress("参数表完成");

// 03 核算项：因子匹配、物料衡算、产生量和治理后排放均为公式。
{
  const sheet = workbook.worksheets.add("03_核算项");
  const headers = payload.item_headers;
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, payload.items);
  const n = payload.items.length;
  const factorLast = payload.factors.length + 1;
  const fuelLast = payload.fuels.length + 1;
  if (n) {
    const end = n + 1;
    const keyMatched = `IFERROR(VLOOKUP(Y2:Y${end},'P_排放因子'!A2:H${factorLast},2,FALSE)=AA2:AA${end},FALSE)`;
    const factorFormula = `=IF(${keyMatched},IF((Z2:Z${end}="constant")+(Z2:Z${end}="constant_sum"),AB2:AB${end},IF(Z2:Z${end}="capacity_lookup",IF(L2:L${end}="","",IF(L2:L${end}<=100,8.96,IF(L2:L${end}<300,8.19,7.21))),IF(Z2:Z${end}="coal_sulfur_balance",IF(((T2:T${end}="")+(U2:U${end}<>"%")+(AE2:AE${end}=""))>0,"",20*T2:T${end}*(1-AE2:AE${end})),IF(Z2:Z${end}="coal_particle_balance",IF(((V2:V${end}="")+(AF2:AF${end}=""))>0,"",IF(W2:W${end}="PM25",10*V2:V${end}*(1-AF2:AF${end})*AG2:AG${end},IF(W2:W${end}="PM10",10*V2:V${end}*(1-AF2:AF${end})*AH2:AH${end},IF(W2:W${end}="BC",10*V2:V${end}*(1-AF2:AF${end})*AG2:AG${end}*AI2:AI${end},IF(W2:W${end}="OC",10*V2:V${end}*(1-AF2:AF${end})*AG2:AG${end}*AJ2:AJ${end},""))))),"")))),"")`;
    sheet.getRange("AK2").formulas = [[factorFormula]];
    sheet.getRange("AM2").formulas = [[`=IF(ISNUMBER(AK2:AK${end})*ISNUMBER(AL2:AL${end}),AK2:AK${end}-AL2:AL${end},"")`]];
    sheet.getRange("AN2").formulas = [[`=IF(C2:C${end}="ammonia_slip",IF(BB2:BB${end}="NH3_COAL_ACTIVITY_MISSING","",SUMIFS('02_燃料明细'!N2:N${fuelLast},'02_燃料明细'!B2:B${fuelLast},B2:B${end},'02_燃料明细'!F2:F${fuelLast},"煤炭")),IFERROR(VLOOKUP(M2:M${end},'02_燃料明细'!A2:U${fuelLast},14,FALSE),""))`]];
    sheet.getRange("AO2").formulas = [[`=IF(AN2:AN${end}="","",IF(C2:C${end}="ammonia_slip","kg",IFERROR(VLOOKUP(M2:M${end},'02_燃料明细'!A2:U${fuelLast},15,FALSE),"")))`]];
    sheet.getRange("AQ2").formulas = [[`=IF(ISNUMBER(AN2:AN${end})*ISNUMBER(AP2:AP${end}),AN2:AN${end}-AP2:AP${end},"")`]];
    sheet.getRange("BC2").formulas = [[`=IF(BA2:BA${end}="allow_calculation",IF(ISNUMBER(AN2:AN${end})*ISNUMBER(AK2:AK${end}),AN2:AN${end}*AK2:AK${end}/1000000,""),IF(BA2:BA${end}="not_applicable",0,""))`]];
    const fineGeneration = `XLOOKUP(X2:X${end},A2:A${end},BC2:BC${end},"")`;
    sheet.getRange("BD2").formulas = [[`=IF(BA2:BA${end}="allow_calculation",IF(W2:W${end}="PM10",IF(ISNUMBER(${fineGeneration})*ISNUMBER(BC2:BC${end}),${fineGeneration}*(1-AW2:AW${end}/100)+(BC2:BC${end}-${fineGeneration})*(1-AX2:AX${end}/100),""),IF(ISNUMBER(BC2:BC${end}),BC2:BC${end}*(1-AV2:AV${end}/100),"")),IF(BA2:BA${end}="not_applicable",0,""))`]];
    sheet.getRange("BG2").formulas = [[`=IF(ISNUMBER(BC2:BC${end})*ISNUMBER(BE2:BE${end}),BC2:BC${end}-BE2:BE${end},"")`]];
    sheet.getRange("BH2").formulas = [[`=IF(ISNUMBER(BD2:BD${end})*ISNUMBER(BF2:BF${end}),BD2:BD${end}-BF2:BF${end},"")`]];

    sheet.getRange(`R2:V${n + 1}`).format.fill = colors.yellow;
    sheet.getRange(`P2:Q${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`Y2:AJ${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`AK2:AK${n + 1}`).format.fill = colors.green;
    sheet.getRange(`AL2:AL${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`AM2:AO${n + 1}`).format.fill = colors.green;
    sheet.getRange(`AP2:AP${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`AQ2:AQ${n + 1}`).format.fill = colors.green;
    sheet.getRange(`AR2:AZ${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`BC2:BH${n + 1}`).format.fill = colors.green;
    sheet.getRange(`R2:V${n + 1}`).format.numberFormat = "0.000000";
    sheet.getRange(`AB2:BH${n + 1}`).format.numberFormat = "0.000000";
    addStatusFormatting(sheet.getRange(`AT2:BB${n + 1}`));
  }
  baseStyle(sheet, headers, n, 2);
  setWidths(sheet, [["A:B", 195], ["C:E", 115], ["F:F", 300], ["G:L", 120], ["M:Q", 175], ["R:BH", 125], ["BI:BJ", 180], ["BK:BL", 310], ["BM:BM", 80]]);
}
progress("核算项完成");

// 04 源结果：只要任一必要核算项被阻断，源×污染物汇总即留空。
{
  const sheet = workbook.worksheets.add("04_源结果");
  const headers = payload.result_headers;
  writeBlock(sheet, 0, 0, [headers]);
  writeBlock(sheet, 1, 0, payload.results);
  const n = payload.results.length;
  const itemLast = payload.items.length + 1;
  if (n) {
    const pollutants = payload.pollutants;
    const fill = (column, formula) => {
      sheet.getRange(`${column}2:${column}${n + 1}`).formulasR1C1 = Array.from({ length: n }, () => [formula]);
    };
    const generationExpressions = [];
    const emissionExpressions = [];
    const statusExpressions = [];
    pollutants.forEach((pollutant) => {
      const pollutantRange = `INDEX('03_核算项'!R2C23:R${itemLast}C23,RC54-1):INDEX('03_核算项'!R2C23:R${itemLast}C23,RC55-1)`;
      const actionRange = `INDEX('03_核算项'!R2C53:R${itemLast}C53,RC54-1):INDEX('03_核算项'!R2C53:R${itemLast}C53,RC55-1)`;
      const generationRange = `INDEX('03_核算项'!R2C55:R${itemLast}C55,RC54-1):INDEX('03_核算项'!R2C55:R${itemLast}C55,RC55-1)`;
      const emissionRange = `INDEX('03_核算项'!R2C56:R${itemLast}C56,RC54-1):INDEX('03_核算项'!R2C56:R${itemLast}C56,RC55-1)`;
      const count = `COUNTIF(${pollutantRange},"${pollutant}")`;
      const withheld = `COUNTIFS(${pollutantRange},"${pollutant}",${actionRange},"withhold")`;
      generationExpressions.push(`IF(${count}=0,"",IF(${withheld}>0,"",SUMIF(${pollutantRange},"${pollutant}",${generationRange})))`);
      emissionExpressions.push(`IF(${count}=0,"",IF(${withheld}>0,"",SUMIF(${pollutantRange},"${pollutant}",${emissionRange})))`);
      statusExpressions.push(`IF(${count}=0,"无核算项",IF(${withheld}>0,"信息不足","已计算"))`);
    });
    fill("J", `=HSTACK(${generationExpressions.concat(emissionExpressions).join(",")})`);
    fill("AK", `=HSTACK(${statusExpressions.join(",")})`);
    sheet.getRange(`J2:AA${n + 1}`).format.fill = colors.green;
    sheet.getRange(`AB2:AJ${n + 1}`).format.fill = colors.blue;
    sheet.getRange(`AK2:AS${n + 1}`).format.fill = colors.green;
    sheet.getRange(`AT2:BA${n + 1}`).format.fill = colors.yellow;
    sheet.getRange(`BB2:BC${n + 1}`).format.fill = colors.gray;
    sheet.getRange(`H2:I${n + 1}`).format.numberFormat = "0.000000";
    sheet.getRange(`J2:BA${n + 1}`).format.numberFormat = "0.000000";
    addStatusFormatting(sheet.getRange(`AK2:AS${n + 1}`));
  }
  baseStyle(sheet, headers, n, 3);
  setWidths(sheet, [["A:A", 190], ["B:B", 300], ["C:G", 125], ["H:I", 110], ["J:BA", 135], ["BB:BC", 100]]);
}
progress("源结果完成");

addParameterSheet("05_异常清单", payload.exception_headers, payload.exceptions,
  [["A:A", 190], ["B:B", 300], ["C:H", 150], ["I:J", 360]]);
addParameterSheet("06_转移排除", payload.ledger_headers, payload.ledger,
  [["A:D", 150], ["E:E", 300], ["F:J", 150], ["K:K", 420]]);
addParameterSheet("07_逐步复核", payload.review_headers, payload.review,
  [["A:A", 70], ["B:B", 220], ["C:C", 520], ["D:F", 135], ["G:H", 270]]);
progress("附属复核表完成");

// Compact verification before export.
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "formula error scan",
});
const itemCheck = await workbook.inspect({
  kind: "table", range: "03_核算项!A1:BH8", include: "values,formulas",
  tableMaxRows: 8, tableMaxCols: 60, maxChars: 40000,
});
const resultCheck = await workbook.inspect({
  kind: "table", range: "04_源结果!A1:BC8", include: "values,formulas",
  tableMaxRows: 8, tableMaxCols: 55, maxChars: 30000,
});
progress("公式检查完成");

await fs.mkdir(previewDir, { recursive: true });
const previewSpecs = [
  ["00_说明", "A1:F19"], ["01_源清单", "A1:Z12"], ["02_燃料明细", "A1:U12"],
  ["03_核算项", "A1:Z12"], ["03_核算项", "AA1:BM12"], ["04_源结果", "A1:Q12"],
  ["04_源结果", "R1:BC12"], ["05_异常清单", "A1:J16"], ["06_转移排除", "A1:K16"],
  ["07_逐步复核", "A1:H14"], ["P_排放因子", "A1:H12"], ["P_燃煤参数", "A1:J12"],
  ["P_治理效率", "A1:G12"], ["P_燃料映射", "A1:F12"], ["P_燃烧映射", "A1:F12"],
  ["P_治理映射", "A1:H12"], ["P_规则索引", "A1:G12"],
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
  output_path: outputPath,
  formula_error_scan: formulaErrors.ndjson,
  item_check: itemCheck.ndjson,
  result_check: resultCheck.ndjson,
  previews,
  sheet_count: workbook.worksheets.items.length,
};
await fs.writeFile(path.join(previewDir, "qa.json"), JSON.stringify(qa, null, 2), "utf8");
process.stdout.write(JSON.stringify({ success: true, output_path: outputPath, preview_dir: previewDir, sheet_count: qa.sheet_count }));
