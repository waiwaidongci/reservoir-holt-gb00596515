import {datasetField, feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {handler} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

const pct = x => `${Number((x * 100).toFixed(1))}%`;
const intervalText = interval => interval.lower === interval.upper
  ? pct(interval.lower) : `${pct(interval.lower)}–${pct(interval.upper)}`;
let receipt = null;

function snapshotPanel(snapshot, current, historical) {
  if (!snapshot) return panel('触线概率区间', empty('还没有已发布的触线概率。请先接收情景预报，再发起情景计算。'));
  const notices = [];
  if (historical) notices.push(pill(`历史版次快照 · 当前发布为第 ${current.published?.version_no ?? '—'} 版`, 'returned'));
  else if (current.newer_pending) notices.push(pill(`第 ${current.latest_version_no} 版已接收，计算完成前仍显示本结果`, 'running'));
  return panel(historical ? `第 ${snapshot.version_no} 版触线概率（已发布快照）` : '当前触线概率',
    el('div', {class:'result-head'}, p(`${snapshot.source} · 第 ${snapshot.version_no} 版 · 发布于 ${date(snapshot.published_at)}`, 'eyeline'),
      h(2, snapshot.headline), p(snapshot.note, 'intro-copy'),
      notices.length && el('div', {class:'actions'}, notices)),
    metrics(snapshot.metrics),
    panel('当时采用的情景', table([
      {key:'scenario', label:'情景'}, {label:'原定权重', render:row=>pct(row.weight)},
      {label:'已报/窗口天数', render:row=>`${row.reported_days}/${row.required_days}`},
      {label:'窗口末日累计（m）', render:row=>value(row.final_level_m)},
      {label:'首次触线', render:row=>row.first_touch || '—'},
      {label:'计入区间', render:row=>({lower:'计入下界', upper:'仅计入上界', none:'未触线'})[row.counts_toward]},
    ], snapshot.scenarios, {caption:'当时采用的情景'})));
}

function runForm(datasets, versions, spec, error) {
  const submit = button('发起情景计算', {type:'submit', class:'primary', 'data-testid':'start-scenario-run'});
  return el('form', {class:'fields', onSubmit:handler(submit, error, async event => {
    const data = new FormData(event.target);
    const item = await workspace.startScenario({
      dataset_id:Number(data.get('dataset_id')), version_id:Number(data.get('version_id')),
      name:data.get('name'),
      params:Object.fromEntries(spec.scenario_params.map(param=>[param.key, data.get(param.key)]))});
    location.hash = `#results/${item.id}`;
  })}, datasetField(datasets),
    el('label', {class:'field', for:'version-id'}, el('span', {}, '预报版次'),
      el('select', {id:'version-id', name:'version_id', required:true}, versions.map(item =>
        el('option', {value:item.id}, `第 ${item.version_no} 版 · ${item.source} · ${item.scenarios} 情景`)))),
    field({key:'name', label:'运行名称', type:'string', max_length:120}, '样本情景触线概率'),
    el('div', {class:'form-grid'}, spec.scenario_params.map(param=>field(param))),
    el('div', {class:'actions'}, submit));
}

function partForm(error, refresh) {
  const submit = button('校验并接收', {type:'submit', class:'primary', 'data-testid':'receive-part'});
  return el('form', {class:'fields', onSubmit:handler(submit, error, async event => {
    const data = new FormData(event.target);
    const file = data.get('part_csv');
    if (!file?.size) throw new Error('请选择一个情景预报 CSV 文件');
    if (file.size > 2_000_000) throw new Error('CSV 最多 2 MB');
    const result = await workspace.receivePart({
      version_no:Number(data.get('version_no')), source:data.get('source'), csv_text:await file.text()});
    receipt = `第 ${result.version_no} 版接收完成：新增 ${result.added} 条，按最新修订更新 ${result.updated} 条，保留 ${result.kept} 条。`;
    await refresh();
  })}, field({key:'version_no', label:'版次号', type:'int', min:1, max:1000000, required:true}),
    field({key:'source', label:'报送来源', type:'string', max_length:120}, '澄湾雨量站'),
    el('label', {class:'field', for:'part-csv'}, el('span', {}, '情景预报 CSV'),
      el('input', {id:'part-csv', type:'file', name:'part_csv', accept:'.csv,text/csv', required:true})),
    el('div', {class:'actions'}, submit), receipt && p(receipt, 'feedback'));
}

export async function handover(route, refresh) {
  const {spec, datasets, runs} = workspace;
  const [current, versions, history] = await Promise.all(
    [workspace.forecastCurrent(), workspace.forecastVersions(), workspace.forecastPublished()]);
  const error = feedback();
  let snapshot = current.published;
  let fetchError = null;
  if (route.id && current.published?.version_no !== route.id) {
    snapshot = await workspace.forecastSnapshot(route.id).catch(() => null);
    if (!snapshot) fetchError = `第 ${route.id} 版没有已发布的触线概率。`;
  }
  const historical = Boolean(snapshot && route.id && snapshot.version_no !== current.published?.version_no);
  const scenarioRuns = runs.filter(run => run.kind === 'scenario');
  const versionNo = id => versions.find(item => item.id === id)?.version_no;
  const main = el('div', {class:'wide-panel'},
    fetchError
      ? panel('触线概率区间', empty(fetchError, '#handover', '查看当前发布'))
      : snapshotPanel(snapshot, current, historical),
    panel('发布历史', table([
      {label:'版次', render:row=>link(`第 ${row.version_no} 版`, `#handover/${row.version_no}`)},
      {label:'概率区间', render:row=>intervalText(row.interval)},
      {label:'预报窗口', render:row=>`${row.window.start} 至 ${row.window.end}`},
      {label:'发布时间', render:row=>date(row.published_at)},
    ], history, {caption:'发布历史', empty:'还没有发布记录。'})),
    panel('情景计算记录', table([
      {label:'运行', render:item=>link(item.name, `#results/${item.id}`)},
      {label:'版次', render:item=>versionNo(item.version_id) ? `第 ${versionNo(item.version_id)} 版` : '—'},
      {label:'状态', render:item=>pill(STATUS[item.status], item.status)},
      {label:'时间', render:item=>date(item.created_at)},
    ], scenarioRuns, {caption:'情景计算记录', empty:'发起情景计算后将在此显示后台状态。'})));
  const aside = el('aside', {class:'sidebar-panel'},
    session.canWrite && panel('发起情景计算',
      datasets.length && versions.length
        ? runForm(datasets, versions, spec, error)
        : empty(versions.length ? '请先导入晨检台账。' : '请先接收情景预报。', versions.length ? '#inputs' : null, versions.length ? '前往导入' : null)),
    session.canWrite && panel('接收情景预报',
      p('同一版次可分批乱序补报，按情景和日期取最新修订。', 'subtle'), partForm(error, refresh)),
    panel('预报版次', table([
      {label:'版次', render:item=>`第 ${item.version_no} 版`},
      {key:'source', label:'来源'}, {key:'scenarios', label:'情景数'}, {key:'entries', label:'条目数'},
      {label:'状态', render:item=>item.published ? pill('已发布', 'complete') : '—'},
    ], versions, {caption:'预报版次', empty:'还没有接收情景预报。'})),
    panel('计算口径', el('ul', {class:'method-list'}, (spec.scenario_methodology || []).map(text=>el('li', {}, text)))),
    error);
  return el('div', {class:'two-col'}, main, aside);
}
