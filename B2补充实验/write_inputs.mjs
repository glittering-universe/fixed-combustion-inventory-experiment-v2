import fs from 'node:fs/promises';
import path from 'node:path';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const payload = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const book = Workbook.create();
const sheet = book.worksheets.add(payload.sheet_name);
const values = payload.values;
sheet.getRangeByIndexes(0, 0, values.length, values[0].length).values = values;
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(1);
sheet.getRangeByIndexes(0, 0, values.length, values[0].length).format.font = {name: '微软雅黑', size: 9};
sheet.getRangeByIndexes(0, 0, values.length, values[0].length).format.columnWidthPx = 130;
sheet.getRange('B:D').format.columnWidthPx = 240;
sheet.getRange('H:H').format.columnWidthPx = 380;
sheet.getRangeByIndexes(0, 0, 1, values[0].length).format = {
  fill: '#1F4E78', font: {name: '微软雅黑', size: 9, bold: true, color: '#FFFFFF'},
  wrapText: true, rowHeightPx: 60,
};
book.recalculate();
const preview = await book.render({sheetName: payload.sheet_name, range: 'A1:H6', scale: 1.5, format: 'png'});
await fs.writeFile(path.join(path.dirname(process.argv[2]), path.basename(payload.output_path) + '.png'), new Uint8Array(await preview.arrayBuffer()));
const out = await SpreadsheetFile.exportXlsx(book);
await out.save(payload.output_path);
console.log(JSON.stringify({file: path.basename(payload.output_path), rows: values.length - 1}));
