import { Customer, Market, colors, money, percent, score, yoyClass, yoyText } from './data';

export function Empty({ children }: { children: React.ReactNode }) { return <div className="wb-empty">{children}</div>; }
export function Ranking({ markets, selected, onSelect }: { markets: Market[]; selected: string; onSelect: (id: string) => void }) {
  return <div className="wb-ranking">{markets.map(m => <button key={m.market} className={selected === m.market ? 'selected' : ''} onClick={() => onSelect(m.market)}><span className="wb-rank-number">{m.rank.toString().padStart(2, '0')}</span><span className="wb-rank-country">{m.name_cn}</span><span className="wb-track"><i style={{ width: `${m.total_score}%` }} /></span><strong>{score(m.total_score)}</strong></button>)}</div>;
}
// Min-max dimension scores collapse to 0/100 once the candidate set is small,
// so the bars read as broken. Show the raw measured metrics the ranking is
// built from instead — comparable across markets at a glance.
export function CompareMarkets({ markets, selected, onSelect }: { markets: Market[]; selected: string; onSelect: (id: string) => void }) {
  return <div className="wb-compare">
    <table>
      <thead><tr><th>市场</th><th>年进口额</th><th>进口同比</th><th>中国供应份额</th><th>适用关税</th><th>GDP 增速</th><th>供应集中度</th><th>综合分</th></tr></thead>
      <tbody>{markets.map(m => <tr key={m.market} className={m.market === selected ? 'current' : ''} onClick={() => onSelect(m.market)}>
        <td><span className={`wb-compare-rank medal-${m.rank <= 3 ? m.rank : 'other'}`}>{m.rank}</span>{m.name_cn}</td>
        <td>{money(m.annual_import)}</td>
        <td className={`wb-yoy ${yoyClass(m.yoy)}`}>{yoyText(m.yoy)}</td>
        <td>{percent(m.china_share)}</td>
        <td>{m.tariff_rate == null ? '—' : `${m.tariff_rate.toFixed(1)}%`}</td>
        <td>{m.dimensions.macro?.raw == null ? '—' : `${m.dimensions.macro.raw.toFixed(1)}%`}</td>
        <td>{m.dimensions.competition?.raw == null ? '—' : m.dimensions.competition.raw.toFixed(2)}</td>
        <td><strong>{score(m.total_score)}</strong></td>
      </tr>)}</tbody>
    </table>
    <p className="wb-note">表中为各项实际测得值（供应集中度为 HHI，越低供应越分散）；综合分由六项候选内相对分加权：市场规模 20% · 增长潜力 20% · 供应竞争 15% · 中国供应 15% · 关税条件 15% · 宏观环境 15%。点击行切换市场。</p>
  </div>;
}
export function Trend({ market }: { market: Market }) {
  const history = market.history || [];
  const forecast = market.forecast;
  if (!history.length || !forecast.periods?.length) return <Empty>这份历史结果缺少日期序列，请让助手重新分析市场以查看趋势。</Empty>;
  const historical = history.slice(-24);
  const values = [...historical.map(p => p.value), ...forecast.values];
  const max = Math.max(...values, ...(forecast.intervals || []).map(p => p.high)) * 1.08;
  const min = Math.min(0, ...(forecast.intervals || []).map(p => p.low));
  const x = (i: number) => 57 + i / (values.length - 1) * 490;
  const y = (v: number) => 191 - (v - min) / (max - min || 1) * 161;
  const line = (vs: number[], offset = 0) => vs.map((v, i) => `${i ? 'L' : 'M'}${x(i + offset)},${y(v)}`).join(' ');
  const boundary = historical.length - 1;
  const intervals = forecast.intervals?.length === forecast.values.length ? forecast.intervals : undefined;
  const area = intervals ? intervals.map((p, i) => `${x(i + historical.length)},${y(p.high)}`).concat([...intervals].reverse().map((p, i) => `${x(values.length - 1 - i)},${y(p.low)}`)).join(' ') : '';
  return <><svg className="wb-chart" viewBox="0 0 570 235" role="img" aria-label={`${market.name_cn}进口历史与未来12个月预测`}>
    {[0, 1, 2, 3].map(i => <g key={i}><line x1="57" x2="547" y1={y(min + (max-min)*i/3)} y2={y(min + (max-min)*i/3)} className="wb-gridline"/><text x="49" y={y(min+(max-min)*i/3)+4} textAnchor="end">{((min+(max-min)*i/3)/1e6).toFixed(0)}</text></g>)}
    <text x="57" y="15">百万美元 / 月</text><rect x={x(boundary)} y="28" width={547-x(boundary)} height="163" fill="#eaf2f8" opacity=".6"/>
    <polygon points={area} fill="#77accd" opacity=".22"/><line x1={x(boundary)} x2={x(boundary)} y1="28" y2="191" stroke="#9bb3c7" strokeDasharray="3 4"/>
    <path d={line(historical.map(p => p.value))} fill="none" stroke="#3979ad" strokeWidth="2.5"/><path d={line([historical[boundary].value, ...forecast.values], boundary)} fill="none" stroke="#3979ad" strokeWidth="2.5" strokeDasharray="5 4"/>
    {values.map((v,i) => <circle key={i} cx={x(i)} cy={y(v)} r="5" fill="transparent"><title>{i < historical.length ? historical[i].period : forecast.periods![i-historical.length]} · {money(v)}{i >= historical.length ? '（预测）' : ''}</title></circle>)}
    <text x="57" y="214">{historical[0].period}</text><text x={x(boundary)} y="214" textAnchor="middle">{historical[boundary].period}</text><text x="547" y="214" textAnchor="end">{forecast.periods[forecast.periods.length - 1]}</text>
  </svg><div className="wb-chart-caption"><span>━ 历史　┄ 预测{intervals ? `　浅色带：${Math.round((forecast.confidence_level || .8)*100)}% 区间` : ''}</span><span>回测 MAPE {forecast.mape.toFixed(1)}%</span></div>{!forecast.enters_market_score && <p className="wb-note">预测误差较高，仅作趋势参考；市场增长评分采用历史增长率。</p>}</>;
}
export function Clusters({ customers }: { customers: Customer[] }) {
  const counts = new Map<string, number>();
  customers.forEach(c => { const label = c.new_customer ? '新客户' : c.cluster_label || '未分群'; counts.set(label, (counts.get(label) || 0)+1); });
  let offset = 0;
  return <div className="wb-donut-layout"><svg viewBox="0 0 160 160" role="img" aria-label="客户分群数量构成"><circle cx="80" cy="80" r="57" fill="none" stroke="#edf2f7" strokeWidth="18"/>{[...counts].map(([label,n], i) => { const length = n/customers.length*358.14; const start = offset; offset += length; return <circle key={label} cx="80" cy="80" r="57" fill="none" stroke={colors[i%colors.length]} strokeWidth="18" strokeDasharray={`${length} ${358.14-length}`} strokeDashoffset={-start} transform="rotate(-90 80 80)"><title>{label}：{n} 家</title></circle>; })}<text x="80" y="78" textAnchor="middle" className="wb-donut-total">{customers.length}</text><text x="80" y="100" textAnchor="middle">客户总数</text></svg><div className="wb-legend">{[...counts].map(([label,n],i) => <div key={label}><i style={{background:colors[i%colors.length]}}/><span>{label}</span><strong>{n}</strong><small>{(n/customers.length*100).toFixed(0)}%</small></div>)}</div></div>;
}
// The scatter read as noise: scores cluster and both axes needed explaining.
// Four quadrant cards state the count, the action and named customers directly.
export function CustomerQuadrants({ customers }: { customers: Customer[] }) {
  const valid = customers.filter(c => c.value_score != null && c.churn_score != null);
  const missing = customers.length - valid.length;
  const quadrants = [
    { tone: 'urgent', name: '高价值 · 高风险', action: '优先维护：2 周内主动回访，排查停单原因并确认保供', test: (c: Customer) => c.value_score! >= 50 && c.churn_score! >= 50 },
    { tone: 'good', name: '高价值 · 低风险', action: '重点保持：按正常节奏维护，供货与新品优先倾斜', test: (c: Customer) => c.value_score! >= 50 && c.churn_score! < 50 },
    { tone: 'warn', name: '低价值 · 高风险', action: '谨慎投入：先评估挽回价值，再决定投入多少人力', test: (c: Customer) => c.value_score! < 50 && c.churn_score! >= 50 },
    { tone: 'calm', name: '低价值 · 低风险', action: '常规维护：邮件与消息自动触达即可，不占用人力', test: (c: Customer) => c.value_score! < 50 && c.churn_score! < 50 },
  ];
  return <><div className="wb-quadrants">{quadrants.map(q => {
    const list = valid.filter(q.test).sort((a, b) => b.value_score! - a.value_score!);
    return <div key={q.tone} className={`wb-quadrant wb-quadrant-${q.tone}`}>
      <header><strong>{list.length}</strong><small>家</small><span>{q.name}</span></header>
      <p>{q.action}</p>
      {list.length > 0 && <div className="wb-quadrant-ids">{list.slice(0, 3).map(c => <span key={c.customer_id}>{c.customer_id}</span>)}{list.length > 3 && <span>+{list.length - 3}</span>}</div>}
    </div>;
  })}</div><p className="wb-note">按价值分与流失风险分以 50 分为界划入四个象限；风险分是相对评分，不是流失概率。{missing > 0 ? ` ${missing} 家评分不完整，未划入。` : ''}</p></>;
}
// Grade tiles explain what each grade means in plain words; the per-market
// stacked bar only returns when there is more than one market to compare.
export function Grades({ customers, markets }: { customers: Customer[]; markets: Market[] }) {
  const grades = ['S','A','B','C','NEW'];
  const info: Record<string, string> = {
    S: '核心客户：交易额与回购频次最高，流失代价最大',
    A: '重点客户：贡献稳定，值得优先供货与倾斜资源',
    B: '常规客户：中等贡献，按既定节奏维护即可',
    C: '长尾客户：贡献较低，适合自动化触达控制成本',
    NEW: '新客户：首单不久，处于培育期，重点做首单复购',
  };
  const ids = [...new Set(customers.map(c => c.country_code))];
  return <>
    <div className="wb-grade-tiles">{grades.map((g, i) => {
      const n = customers.filter(c => c.value_grade === g).length;
      return <div key={g}><span><i style={{background:colors[i]}} />{g==='NEW'?'新客户':g+'级'}</span><strong>{n}<small> 家</small></strong><small className="wb-grade-share">{customers.length?Math.round(n/customers.length*100):0}%</small><p>{info[g]}</p></div>;
    })}</div>
    {ids.length > 1 && <div className="wb-grade-bars">{ids.map(id => { const group = customers.filter(c => c.country_code === id); return <div key={id}><span>{markets.find(m=>m.market===id)?.name_cn || id}</span><div>{grades.map((grade,i) => { const n = group.filter(c => c.value_grade === grade).length; return n ? <span key={grade} style={{width:`${n/group.length*100}%`,background:colors[i]}} title={`${grade==='NEW'?'新客户':grade+'级'}：${n} 家`}>{n/group.length>.12 ? n : ''}</span> : null; })}</div><small>{group.length} 家</small></div>; })}</div>}
  </>;
}
