"use strict";
const state={symbol:"005930",stock:null,stocks:[],rawBars:[],bars:[],chartRange:"1d",interval:"1m",chartMode:"candle",selectionMode:"inspect",selectedBuy:null,selectedSell:null,snapshot:null,trades:[],portfolio:null,settings:null,ws:null,selectedTradeId:null,currentTick:null,setup:null,aiConfig:null,aiAnalysis:null,semiAutoPlaybook:null,aiJobId:null,aiPoll:null,aiRequestedTradeId:null,aiAutoAppliedId:null,lastAutoPrompt:null,alertNotified:new Set(),newsOverview:null,selectedNewsCluster:null,virtualAuto:{armed:false,busy:false,lastActionAt:0},featureLayout:{pinned:[],excluded:[],ai_order:[],mode:"default"},manualThresholds:{}};
const $=id=>document.getElementById(id);
const fmt=(v,d=0)=>v===null||v===undefined||Number.isNaN(Number(v))?"-":Number(v).toLocaleString("ko-KR",{maximumFractionDigits:d,minimumFractionDigits:d});
const signed=(v,d=2)=>`${Number(v)>=0?"+":""}${fmt(v,d)}`;
const fmtMoney=v=>v===null||v===undefined||Number.isNaN(Number(v))?"-":`₩${fmt(v,0)}`;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(url,options={}){const res=await fetch(url,{headers:{"Content-Type":"application/json",...(options.headers||{})},...options});let body;try{body=await res.json()}catch{body={detail:await res.text()}}if(!res.ok)throw new Error(body.detail||body.error||`HTTP ${res.status}`);return body}
function setStatus(text,type="normal"){$("bottom-status").textContent=text;$('connection-text').textContent=text;$('connection-dot').className=`status-dot ${type==='live'?'live':type==='error'?'error':''}`}
function toast(msg,error=false){const el=$('order-message');el.textContent=msg;el.className=`order-message ${error?'negative':'positive'}`;setTimeout(()=>el.className='order-message',3500)}
function startClock(){const tick=()=>$('clock').textContent=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour12:false,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date());tick();setInterval(tick,1000)}
function renderStockTabs(query=''){const q=query.trim().toLowerCase();const stocks=state.stocks.filter(s=>!q||`${s.name} ${s.symbol} ${s.sector}`.toLowerCase().includes(q));$('stock-tabs').innerHTML=stocks.length?stocks.map(s=>`<button class="stock-tab ${s.symbol===state.symbol?'active':''}" data-symbol="${s.symbol}"><strong>${esc(s.name)}</strong><small>${esc(s.symbol)}</small></button>`).join(''):'<span class="search-empty">검색 결과 없음</span>';document.querySelectorAll('.stock-tab').forEach(b=>b.onclick=()=>{ $('stock-search').value='';switchSymbol(b.dataset.symbol)})}
function resample(rows,min){if(min===1)return rows;const out=new Map();for(const r of rows){const d=new Date(r.time);d.setSeconds(0,0);d.setMinutes(Math.floor(d.getMinutes()/min)*min);const k=d.toISOString();if(!out.has(k))out.set(k,{time:k,open:+r.open,high:+r.high,low:+r.low,close:+r.close,volume:+r.volume});else{const b=out.get(k);b.high=Math.max(b.high,+r.high);b.low=Math.min(b.low,+r.low);b.close=+r.close;b.volume+=+r.volume}}return[...out.values()]}
function activeBars(){return state.chartRange==='1d'||state.chartRange==='1w'?resample(state.rawBars,parseInt(state.interval,10)||1):state.rawBars}
let chartRangeAdjusting=false;
// The range label is not the bar resolution: KIS returns daily bars for its
// week/month history.  Treating those as minute bars hid every 15:30 daily bar.
state.barResolution='intraday';state.chartCache=new Map();state.chartRequestId=0;state.chartAbort=null;
let chartRenderFrame=0,chartRenderPreserve=false;
function chartCacheKey(symbol,range){return `${symbol}:${range}`}
function detectChartResolution(rows){const seen=new Set();for(const row of rows||[]){const date=new Date(row.time);if(!Number.isFinite(date.getTime()))continue;const key=date.toISOString().slice(0,10);if(seen.has(key))return 'intraday';seen.add(key)}return 'daily'}
function kstTime(date){const shifted=new Date(date.getTime()+9*60*60*1000);return shifted.toISOString().replace('Z','+09:00')}
function normalizeChartBars(rows){const unique=new Map();for(const row of rows||[]){const date=new Date(row.time),open=Number(row.open),high=Number(row.high),low=Number(row.low),close=Number(row.close);if(!Number.isFinite(date.getTime())||![open,high,low,close].every(Number.isFinite)||close<=0)continue;const bar={time:kstTime(date),open,high:Math.max(high,open,close),low:Math.min(low,open,close),close,volume:Math.max(0,Number(row.volume)||0)};unique.set(bar.time,bar)}return [...unique.values()].sort((a,b)=>new Date(a.time)-new Date(b.time))}
function resampleKst(rows,min){if(min===1)return rows;const out=new Map();for(const r of rows){const date=new Date(r.time);if(!Number.isFinite(date.getTime()))continue;const local=new Date(date.getTime()+9*60*60*1000);local.setUTCSeconds(0,0);local.setUTCMinutes(Math.floor(local.getUTCMinutes()/min)*min);const key=local.toISOString().replace('Z','+09:00');if(!out.has(key))out.set(key,{time:key,open:+r.open,high:+r.high,low:+r.low,close:+r.close,volume:+r.volume});else{const bar=out.get(key);bar.high=Math.max(bar.high,+r.high);bar.low=Math.min(bar.low,+r.low);bar.close=+r.close;bar.volume+=+r.volume}}return [...out.values()]}
activeBars=function(){return state.barResolution==='intraday'?resampleKst(state.rawBars,parseInt(state.interval,10)||1):state.rawBars}
function scheduleChartRender(preserve=false){chartRenderPreserve=chartRenderPreserve||preserve;if(chartRenderFrame)return;chartRenderFrame=requestAnimationFrame(()=>{const keep=chartRenderPreserve;chartRenderFrame=0;chartRenderPreserve=false;renderChart(keep)})}
renderChart=function(preserve=false){const rows=activeBars(),div=$('main-chart');state.bars=rows;if(!rows.length){$('hover-info').textContent='표시할 유효한 OHLCV 데이터가 없습니다.';return Plotly.react(div,[],{paper_bgcolor:'#0c111a',plot_bgcolor:'#0c111a',font:{color:'#8593a5'},annotations:[{text:'차트 데이터를 불러오지 못했습니다.',showarrow:false,font:{color:'#8593a5',size:13}}],xaxis:{visible:false},yaxis:{visible:false}},{displaylogo:false,responsive:true})}const x=rows.map(r=>r.time),close=rows.map(r=>r.close),traces=[];if(state.chartMode==='candle')traces.push({type:'candlestick',x,open:rows.map(r=>r.open),high:rows.map(r=>r.high),low:rows.map(r=>r.low),close,increasing:{line:{color:'#f0526f',width:1},fillcolor:'#f0526f'},decreasing:{line:{color:'#3b82f6',width:1},fillcolor:'#3b82f6'},name:'Price',yaxis:'y'});else traces.push({type:'scattergl',mode:'lines',x,y:close,line:{color:'#d8e1ee',width:1.6},name:'Price',yaxis:'y'});traces.push({type:'bar',x,y:rows.map(r=>r.volume),marker:{color:rows.map(r=>r.close>=r.open?'rgba(240,82,111,.42)':'rgba(59,130,246,.42)')},name:'Volume',yaxis:'y2',hoverinfo:'skip'});if(state.selectedBuy)traces.push({type:'scatter',mode:'markers+text',x:[state.selectedBuy.time],y:[state.selectedBuy.price],text:['B'],textposition:'bottom center',marker:{size:11,color:'#00d89a',symbol:'triangle-up'},textfont:{color:'#00d89a'},showlegend:false});if(state.selectedSell)traces.push({type:'scatter',mode:'markers+text',x:[state.selectedSell.time],y:[state.selectedSell.price],text:['S'],textposition:'top center',marker:{size:11,color:'#ff4d6d',symbol:'triangle-down'},textfont:{color:'#ff4d6d'},showlegend:false});for(const t of state.trades.filter(t=>t.symbol===state.symbol).slice(0,80)){traces.push({type:'scatter',mode:'markers',x:[t.entry_time],y:[t.entry_price],marker:{size:7,color:'#00d89a',symbol:'triangle-up'},showlegend:false});if(t.exit_time)traces.push({type:'scatter',mode:'markers',x:[t.exit_time],y:[t.exit_price],marker:{size:7,color:'#ff4d6d',symbol:'triangle-down'},showlegend:false})}const old=div.layout,isIntraday=state.barResolution==='intraday',breaks=isIntraday?[{bounds:['sat','mon']},{pattern:'hour',bounds:[15.5,9]}]:[{bounds:['sat','mon']}],layout={paper_bgcolor:'#0c111a',plot_bgcolor:'#0c111a',font:{color:'#8593a5',size:10},margin:{l:8,r:78,t:8,b:28},showlegend:false,dragmode:'pan',hovermode:'x unified',xaxis:{gridcolor:'#1a2533',zeroline:false,rangeslider:{visible:false},showspikes:true,spikemode:'across',spikesnap:'cursor',spikecolor:'#68788c',spikethickness:1,tickformat:isIntraday?'%m/%d\n%H:%M':'%Y/%m/%d',rangebreaks:breaks},yaxis:{domain:[.23,1],side:'right',gridcolor:'#1a2533',zeroline:false,tickprefix:'₩',tickformat:',d'},yaxis2:{domain:[0,.18],side:'right',gridcolor:'#151f2c',zeroline:false,tickformat:'~s',fixedrange:true},shapes:shapes(),uirevision:preserve?`${state.symbol}-${state.chartRange}-${state.interval}`:undefined};if(preserve&&old?.xaxis?.range)layout.xaxis.range=old.xaxis.range;const draw=Plotly.react(div,traces,layout,{displaylogo:false,responsive:true,scrollZoom:true,modeBarButtonsToRemove:['lasso2d','select2d']});div.removeAllListeners?.('plotly_click');div.removeAllListeners?.('plotly_hover');div.removeAllListeners?.('plotly_relayout');div.on('plotly_click',onChartClick);div.on('plotly_hover',onChartHover);div.on('plotly_relayout',clampFuturePan);return draw}
updateBar=function(t){if(!t?.price||String(t.quality||'').includes('CLOSED')||!state.rawBars.length)return;const price=Number(t.price),last=state.rawBars.at(-1);if(!Number.isFinite(price))return;if(state.barResolution!=='intraday'){last.high=Math.max(+last.high,price);last.low=Math.min(+last.low,price);last.close=price;scheduleChartRender(true);return}const d=new Date(t.time);d.setSeconds(0,0);const key=kstTime(d);if(new Date(last.time).toISOString().slice(0,16)===d.toISOString().slice(0,16)){last.high=Math.max(+last.high,price);last.low=Math.min(+last.low,price);last.close=price;last.volume=+last.volume+(+t.volume||0)}else{const prev=+last.close||price;state.rawBars.push({time:key,open:prev,high:Math.max(prev,price),low:Math.min(prev,price),close:price,volume:+t.volume||0});state.rawBars=state.rawBars.slice(-2500)}scheduleChartRender(true)}
function applyChartPayload(data,range){const rows=normalizeChartBars(data.bars);state.rawBars=rows;state.barResolution=data.bar_resolution==='intraday'||data.bar_resolution==='daily'?data.bar_resolution:detectChartResolution(rows);$('interval-controls').classList.toggle('hidden',state.barResolution!=='intraday');state.chartCache.set(chartCacheKey(state.symbol,range),{...data,bars:rows});$('data-source').textContent=data.source||'-';renderChart(false)}
async function warmChartRanges(symbol){for(const range of ['1w','1mo','3mo']){if(symbol!==state.symbol||state.chartCache.has(chartCacheKey(symbol,range)))continue;try{const data=await api(`/api/chart/${encodeURIComponent(symbol)}?chart_range=${range}`);if(symbol===state.symbol)state.chartCache.set(chartCacheKey(symbol,range),{...data,bars:normalizeChartBars(data.bars)})}catch(error){console.debug('chart prefetch failed',range,error)}}}
const baseLoadSymbol=loadSymbol;
loadSymbol=async function(symbol,initial=false){await baseLoadSymbol(symbol,initial);if(symbol!==state.symbol)return;state.rawBars=normalizeChartBars(state.rawBars);state.barResolution=detectChartResolution(state.rawBars);$('interval-controls').classList.toggle('hidden',state.barResolution!=='intraday');state.chartCache.set(chartCacheKey(symbol,state.chartRange),{bars:state.rawBars,bar_resolution:state.barResolution});renderChart(false);setTimeout(()=>warmChartRanges(symbol),0)};
changeChartRange=async function(chartRange){if(chartRange===state.chartRange)return;state.chartRange=chartRange;state.selectedBuy=null;state.selectedSell=null;document.querySelectorAll('#range-controls button').forEach(x=>x.classList.toggle('active',x.dataset.range===chartRange));$('interval-controls').classList.toggle('hidden',chartRange==='1mo'||chartRange==='3mo');const requestId=++state.chartRequestId;state.chartAbort?.abort();const cached=state.chartCache.get(chartCacheKey(state.symbol,chartRange));if(cached){applyChartPayload(cached,chartRange);setStatus('최근 차트 표시 · 최신 데이터 확인 중')}else setStatus('시계열 불러오는 중');const controller=new AbortController();state.chartAbort=controller;try{const data=await api(`/api/chart/${encodeURIComponent(state.symbol)}?chart_range=${encodeURIComponent(chartRange)}`,{signal:controller.signal});if(requestId!==state.chartRequestId||state.symbol!==data.symbol)return;applyChartPayload(data,chartRange);setStatus(state.settings?.data_mode==='kis'?'KIS 실시간 연결':state.settings?.data_mode==='public'?'무키 실데이터 연결':'DEMO 시뮬레이션','live')}catch(error){if(error?.name==='AbortError')return;setStatus(error.message,'error');toast(error.message,true)}};
function clampFuturePan(event){
  if(chartRangeAdjusting||!state.bars.length)return;
  const range=event['xaxis.range']||((event['xaxis.range[0]']&&event['xaxis.range[1]'])?[event['xaxis.range[0]'],event['xaxis.range[1]']]:null);
  if(!range)return;
  const start=new Date(range[0]).getTime(),end=new Date(range[1]).getTime(),latest=new Date(state.bars.at(-1).time).getTime();
  if(!Number.isFinite(start)||!Number.isFinite(end)||end<=latest)return;
  const span=end-start;chartRangeAdjusting=true;
  Plotly.relayout('main-chart',{'xaxis.range[0]':new Date(latest-span).toISOString(),'xaxis.range[1]':new Date(latest).toISOString()}).finally(()=>{chartRangeAdjusting=false});
}
function shapes(){const a=[];if(state.selectedBuy)a.push({type:'line',xref:'paper',x0:0,x1:1,y0:state.selectedBuy.price,y1:state.selectedBuy.price,line:{color:'#00d89a',width:1,dash:'dot'}});if(state.selectedSell)a.push({type:'line',xref:'paper',x0:0,x1:1,y0:state.selectedSell.price,y1:state.selectedSell.price,line:{color:'#ff4d6d',width:1,dash:'dot'}});if(state.currentTick?.price)a.push({type:'line',xref:'paper',x0:0,x1:1,y0:state.currentTick.price,y1:state.currentTick.price,line:{color:'#d5deea',width:1,dash:'dash'}});return a}
function renderChart(preserve=false){const rows=activeBars();state.bars=rows;if(!rows.length)return;const x=rows.map(r=>r.time),close=rows.map(r=>r.close),traces=[];if(state.chartMode==='candle')traces.push({type:'candlestick',x,open:rows.map(r=>r.open),high:rows.map(r=>r.high),low:rows.map(r=>r.low),close,increasing:{line:{color:'#f0526f',width:1},fillcolor:'#f0526f'},decreasing:{line:{color:'#3b82f6',width:1},fillcolor:'#3b82f6'},name:'가격',yaxis:'y'});else traces.push({type:'scattergl',mode:'lines',x,y:close,line:{color:'#d8e1ee',width:1.6},name:'가격',yaxis:'y'});traces.push({type:'bar',x,y:rows.map(r=>r.volume),marker:{color:rows.map(r=>r.close>=r.open?'rgba(240,82,111,.42)':'rgba(59,130,246,.42)')},name:'거래량',yaxis:'y2',hoverinfo:'skip'});if(state.selectedBuy)traces.push({type:'scatter',mode:'markers+text',x:[state.selectedBuy.time],y:[state.selectedBuy.price],text:['B'],textposition:'bottom center',marker:{size:11,color:'#00d89a',symbol:'triangle-up'},textfont:{color:'#00d89a'},showlegend:false});if(state.selectedSell)traces.push({type:'scatter',mode:'markers+text',x:[state.selectedSell.time],y:[state.selectedSell.price],text:['S'],textposition:'top center',marker:{size:11,color:'#ff4d6d',symbol:'triangle-down'},textfont:{color:'#ff4d6d'},showlegend:false});for(const t of state.trades.filter(t=>t.symbol===state.symbol).slice(0,80)){traces.push({type:'scatter',mode:'markers',x:[t.entry_time],y:[t.entry_price],marker:{size:7,color:'#00d89a',symbol:'triangle-up'},hovertemplate:`거래 #${t.id}<br>매수 %{y:,.0f}원<extra></extra>`,showlegend:false});if(t.exit_time)traces.push({type:'scatter',mode:'markers',x:[t.exit_time],y:[t.exit_price],marker:{size:7,color:'#ff4d6d',symbol:'triangle-down'},hovertemplate:`거래 #${t.id}<br>매도 %{y:,.0f}원<extra></extra>`,showlegend:false})}const div=$('main-chart'),old=div.layout,isIntraday=state.chartRange==='1d'||state.chartRange==='1w',breaks=isIntraday?[{bounds:['sat','mon']},{pattern:'hour',bounds:[15.5,9]}]:[{bounds:['sat','mon']}];const layout={paper_bgcolor:'#0c111a',plot_bgcolor:'#0c111a',font:{color:'#8593a5',size:10},margin:{l:8,r:78,t:8,b:28},showlegend:false,dragmode:'pan',hovermode:'x unified',xaxis:{gridcolor:'#1a2533',zeroline:false,rangeslider:{visible:false},showspikes:true,spikemode:'across',spikesnap:'cursor',spikecolor:'#68788c',spikethickness:1,tickformat:isIntraday?'%m/%d\n%H:%M':'%Y/%m/%d',rangebreaks:breaks},yaxis:{domain:[.23,1],side:'right',gridcolor:'#1a2533',zeroline:false,tickprefix:'₩',tickformat:',d'},yaxis2:{domain:[0,.18],side:'right',gridcolor:'#151f2c',zeroline:false,tickformat:'~s',fixedrange:true},shapes:shapes(),uirevision:preserve?`${state.symbol}-${state.chartRange}-${state.interval}`:undefined};if(preserve&&old?.xaxis?.range)layout.xaxis.range=old.xaxis.range;Plotly.react(div,traces,layout,{displaylogo:false,responsive:true,scrollZoom:true,modeBarButtonsToRemove:['lasso2d','select2d']});div.removeAllListeners?.('plotly_click');div.removeAllListeners?.('plotly_hover');div.removeAllListeners?.('plotly_relayout');div.on('plotly_click',onChartClick);div.on('plotly_hover',onChartHover);div.on('plotly_relayout',clampFuturePan)}
function onChartClick(e){const p=e.points?.find(x=>x.yaxis?._id==='y')||e.points?.[0];if(!p)return;const time=new Date(p.x).toISOString(),price=Number(p.y??p.close??p.data?.close?.[p.pointNumber]??state.bars[p.pointNumber]?.close);if(!Number.isFinite(price))return toast('선택한 봉의 가격을 읽지 못했습니다.',true);if(state.selectionMode==='buy')selectPoint('buy',time,price);else if(state.selectionMode==='sell')selectPoint('sell',time,price);else $('order-message').textContent=`조회 ${new Date(time).toLocaleString('ko-KR')} · ${fmt(price)}원`}
function onChartHover(e){const p=e.points?.[0],r=state.bars[p?.pointNumber];if(r)$('hover-info').textContent=`${new Date(r.time).toLocaleString('ko-KR')}  O ${fmt(r.open)} H ${fmt(r.high)} L ${fmt(r.low)} C ${fmt(r.close)}  V ${fmt(r.volume)}`}
function selectPoint(side,time,price){const o={time,price};if(side==='buy')state.selectedBuy=o;else state.selectedSell=o;$(side==='buy'?'buy-price':'sell-price').value=Math.round(price);$(side==='buy'?'buy-time':'sell-time').value=new Date(time).toLocaleString('ko-KR');$(side==='buy'?'selected-buy':'selected-sell').textContent=`${side==='buy'?'매수':'매도'}: ${fmt(price)} · ${new Date(time).toLocaleTimeString('ko-KR',{hour12:false})}`;updateQuantity();renderChart(true)}
function clearSelections(){state.selectedBuy=null;state.selectedSell=null;$('selected-buy').textContent='매수: 미지정';$('selected-sell').textContent='매도: 미지정';$('buy-time').value='';$('sell-time').value='';renderChart(true)}
function updateHeader(t){if(!t||t.error)return;state.currentTick=t;const cls=Number(t.change_rate)>=0?'up':'down',ask=t.ask??t.orderbook?.asks?.[0]?.price,bid=t.bid??t.orderbook?.bids?.[0]?.price;$('current-price').textContent=fmtMoney(t.price);$('current-change').className=cls;$('current-change').textContent=`${signed(t.change||0,0)}원 (${signed(t.change_rate||0,2)}%)`;$('depth-price').textContent=fmtMoney(t.price);$('best-ask').textContent=ask==null?'미제공':fmtMoney(ask);$('best-bid').textContent=bid==null?'미제공':fmtMoney(bid);$('data-source').textContent=t.source||'-';$('depth-quality').textContent=t.orderbook?.quality||t.quality||'호가 미제공';renderOrderbook(t)}
function updateBar(t){if(!t?.price||String(t.quality||'').includes('CLOSED'))return;const d=new Date(t.time);d.setSeconds(0,0);const key=d.toISOString(),last=state.rawBars.at(-1);if(!last)return;if(state.chartRange==='1mo'||state.chartRange==='3mo'){last.high=Math.max(+last.high,+t.price);last.low=Math.min(+last.low,+t.price);last.close=+t.price;renderChart(true);return}if(last&&new Date(last.time).toISOString().slice(0,16)===key.slice(0,16)){last.high=Math.max(+last.high,+t.price);last.low=Math.min(+last.low,+t.price);last.close=+t.price;last.volume=+last.volume+(+t.volume||0)}else{const prev=last?+last.close:+t.price;state.rawBars.push({time:key,open:prev,high:Math.max(prev,+t.price),low:Math.min(prev,+t.price),close:+t.price,volume:+t.volume||0});state.rawBars=state.rawBars.slice(-2500)}renderChart(true)}
function renderOrderbook(t){
  const price=+t.price||0;let rows=[];
  if(t.orderbook?.asks?.length||t.orderbook?.bids?.length){
    const asks=(t.orderbook.asks||[]).slice(0,5).reverse().map(r=>({side:'ask',price:+r.price,qty:+r.qty||0}));
    const bids=(t.orderbook.bids||[]).slice(0,5).map(r=>({side:'bid',price:+r.price,qty:+r.qty||0}));rows=[...asks,...bids];
  }
  if(!rows.length){$('orderbook').innerHTML='<div class="analysis-empty">실제 호가 미제공 · 가상 호가를 생성하지 않습니다.</div>';return}
  const max=Math.max(1,...rows.map(r=>r.qty));$('orderbook').innerHTML=rows.map(r=>`<div class="book-row ${r.side}"><div class="bar" style="width:${r.qty/max*48}%"></div><span class="qty ${r.side==='ask'?'right':''}">${r.side==='ask'?fmt(r.qty):''}</span><span class="price">${fmtMoney(r.price)}</span><span class="qty right">${r.side==='bid'?fmt(r.qty):''}</span></div>`).join('')
}
function renderMembers(rows=[]){let h='<div class="member-row header"><span>회원사/창구</span><span>매수</span><span>매도</span><span>순매수</span></div>';h+=rows.map(r=>`<div class="member-row"><span>${esc(r.name)}</span><span>${fmt(r.buy)}</span><span>${fmt(r.sell)}</span><span class="${r.net>=0?'positive':'negative'}">${signed(r.net,0)}</span></div>`).join('');$('member-flow').innerHTML=h}
function manualThresholdStorageKey(){return `kr-terminal-manual-thresholds:${state.symbol}`}
function loadManualThresholds(){try{state.manualThresholds=JSON.parse(localStorage.getItem(manualThresholdStorageKey())||'{}')}catch{state.manualThresholds={}}}
function manualOperatorText(operator){return({GT:'>',GTE:'>=',LT:'<',LTE:'<=',BETWEEN:'~'})[operator]||''}
function manualThresholdText(item){const threshold=state.manualThresholds?.[item?.feature_id||item?.key];if(!threshold)return'';const unit=item?.unit||'';const render=rule=>rule&&Number.isFinite(Number(rule.value1))?`${manualOperatorText(rule.operator)}${fmt(Number(rule.value1),Math.abs(Number(rule.value1))<10?3:1)}${rule.operator==='BETWEEN'&&Number.isFinite(Number(rule.value2))?`~${fmt(Number(rule.value2),3)}`:''}${unit}`:'';const buy=render(threshold.buy),sell=render(threshold.sell);return buy||sell?` (매수 ${buy||'-'} / 매도 ${sell||'-'})`:''}
function displayFeatureValue(item){const v=item?.value;if(v===null||v===undefined||v==='')return item?.metadata?.required_api?`API 필요: ${esc(item.metadata.required_api)}`:'미연결';const base=typeof v==='number'&&item.unit==='원'?fmtMoney(v):typeof v==='number'?`${fmt(v,Math.abs(v)<10?3:1)}${item.unit||''}`:`${esc(v)}${item.unit||''}`;return `${base}${manualThresholdText(item)}`}
function featureToneClass(direction){const d=String(direction||'').toLowerCase();return d==='positive'?'positive':d==='negative'?'negative':d==='warning'||d==='caution'||d==='conflicted'?'warning':d==='unknown'?'unknown':''}
function layoutKey(){return `kr-terminal-feature-layout:${state.symbol}`}
function readLocalLayout(){try{return JSON.parse(localStorage.getItem(layoutKey())||'null')}catch{return null}}
function normalizeLayout(value={}){return{symbol:state.symbol,pinned:[...new Set(value.pinned||[])],excluded:[...new Set(value.excluded||[])],ai_order:[...new Set(value.ai_order||[])],mode:value.mode||'default'}}
async function persistFeatureLayout(){
  state.featureLayout=normalizeLayout(state.featureLayout);localStorage.setItem(layoutKey(),JSON.stringify(state.featureLayout));
  try{state.featureLayout=normalizeLayout(await api('/api/ai/feature-layout',{method:'POST',body:JSON.stringify(state.featureLayout)}));localStorage.setItem(layoutKey(),JSON.stringify(state.featureLayout))}catch(e){console.warn('피처 배열 DB 저장 실패',e.message)}
}
function alertMatches(alert,value){if(typeof value!=='number'||alert.threshold===null||alert.threshold===undefined)return false;const t=Number(alert.threshold),s=Number(alert.second_threshold);return alert.operator==='GT'?value>t:alert.operator==='GTE'?value>=t:alert.operator==='LT'?value<t:alert.operator==='LTE'?value<=t:alert.operator==='BETWEEN'?value>=t&&value<=s:false}
function featureAlertState(item,snapshot){
  const alerts=snapshot?.active_alerts||[],match=alerts.some(a=>a.feature_id===(item.feature_id||item.key)&&alertMatches(a,Number(item.value)));
  const triggered=(snapshot?.triggered_alerts||[]).some(a=>a.feature_id===(item.feature_id||item.key));
  return{active:alerts.some(a=>a.feature_id===(item.feature_id||item.key)),matched:match||triggered}
}
function featureSortScore(item,defaultIndex){
  const id=item.feature_id||item.key,l=state.featureLayout||{},pin=l.pinned.indexOf(id),excluded=l.excluded.includes(id),ai=l.ai_order.indexOf(id);
  if(pin>=0)return pin-100000;
  const marketRank=(state.snapshot?.market_promoted_features||[]).indexOf(id);
  if(marketRank>=0)return-90000+marketRank;
  const newsRank=(state.newsOverview?.promoted_features||[]).indexOf(id);
  if(newsRank>=0)return-80000+newsRank;
  const risk=(item.status==='NEGATIVE'?4:item.status==='CAUTION'||item.status==='CONFLICTED'?3:item.status==='UNKNOWN'?1:0);
  const importance=state.aiAnalysis?.top_features?.find(x=>x.feature_id===id)?.importance||0;
  if(l.mode==='ai'&&!excluded&&ai>=0)return-50000+ai;
  if(l.mode==='importance'&&!excluded&&importance)return-40000-importance;
  if(l.mode==='risk'&&risk)return-30000-risk;
  return defaultIndex;
}
function renderFeatures(snapshot){
  if(!snapshot)return;state.snapshot=snapshot;
  $('snapshot-time').textContent=new Date(snapshot.time).toLocaleTimeString('ko-KR',{hour12:false});
  $('data-notice').textContent=snapshot.data_notice||'';
  renderMembers(snapshot.members||[]);
  const groups=snapshot.groups||{};
  const filter=$('feature-group-filter');
  const current=filter.value||'all';
  filter.innerHTML='<option value="all">전체 그룹</option>'+Object.keys(groups).map(g=>`<option value="${esc(g)}">${esc(g)}</option>`).join('');
  filter.value=Object.prototype.hasOwnProperty.call(groups,current)?current:'all';
  const search=($('feature-search').value||'').trim().toLowerCase();
  const selected=filter.value;
  let html='',globalIndex=0;
  let entries=Object.entries(groups).map(([group,items],groupIndex)=>{
    const ranked=(items||[]).map((item,itemIndex)=>({item,score:featureSortScore(item,globalIndex++),itemIndex})).sort((a,b)=>a.score-b.score||a.itemIndex-b.itemIndex);
    return{group,items:ranked.map(x=>x.item),groupIndex,score:ranked.length?ranked[0].score:999999}
  });
  if(state.featureLayout.mode!=='default')entries.sort((a,b)=>a.score-b.score||a.groupIndex-b.groupIndex);
  for(const entry of entries){
    const group=entry.group,items=entry.items;
    if(selected!=='all'&&selected!==group)continue;
    const visible=(items||[]).filter(i=>!search||`${i.label} ${i.key} ${i.source||''}`.toLowerCase().includes(search));
    if(!visible.length)continue;
    html+=`<section class="feature-group"><div class="feature-group-title"><span>${esc(group)}</span><span>${visible.length}</span></div>`;
    html+=visible.map(i=>{const id=i.feature_id||i.key,pinned=state.featureLayout.pinned.includes(id),excluded=state.featureLayout.excluded.includes(id),ranked=state.featureLayout.ai_order.includes(id),newsLinked=(state.newsOverview?.promoted_features||[]).includes(id),marketLinked=(snapshot.market_promoted_features||[]).includes(id),alert=featureAlertState(i,snapshot);return`<div class="feature-row ${ranked?'ai-ranked':''} ${marketLinked?'market-linked':''} ${newsLinked?'news-linked':''} ${alert.active?'alert-active':''} ${alert.matched?'alert-triggered':''} ${i.status==='UNKNOWN'?'unknown':''}" data-feature-id="${esc(id)}" title="출처: ${esc(i.source||'-')} · 상태: ${esc(i.quality||'-')} · ${marketLinked?'주요 시장·동종주 피처 · ':''}${newsLinked?'현재 뉴스 연계 상단 피처 · ':''}${esc(i.explanation||'')}"><span class="feature-label-wrap"><button class="feature-mini-action ${pinned?'active':''}" data-pin="${esc(id)}" title="피처 고정">${pinned?'★':'☆'}</button><button class="feature-mini-action excluded ${excluded?'active':''}" data-exclude="${esc(id)}" title="AI 정렬 ${excluded?'포함':'제외'}">${excluded?'×':'A'}</button><span class="feature-label">${esc(i.label)}${marketLinked?'<em class="market">MKT</em>':''}${newsLinked?'<em>NEWS</em>':''}</span></span><strong class="${featureToneClass(i.status||i.direction)}">${displayFeatureValue(i)}</strong><small>${esc(i.quality||'')}</small></div>`}).join('');
    html+='</section>';
  }
  $('feature-groups').innerHTML=html||'<div class="analysis-empty">검색 결과가 없습니다.</div>';
  document.querySelectorAll('[data-pin]').forEach(b=>b.onclick=e=>{e.stopPropagation();const id=b.dataset.pin,list=state.featureLayout.pinned;state.featureLayout.pinned=list.includes(id)?list.filter(x=>x!==id):[...list,id];persistFeatureLayout();renderFeatures(state.snapshot)});
  document.querySelectorAll('[data-exclude]').forEach(b=>b.onclick=e=>{e.stopPropagation();const id=b.dataset.exclude,list=state.featureLayout.excluded;state.featureLayout.excluded=list.includes(id)?list.filter(x=>x!==id):[...list,id];persistFeatureLayout();renderFeatures(state.snapshot)});
  renderAlertBanner(snapshot);
  renderMarketWatch(snapshot);
  renderRadar(snapshot.flat||{});
  renderAiLiveContext();
  if(snapshot.latest_ai_analysis&&snapshot.latest_ai_analysis.analysis_id!==state.aiAnalysis?.analysis_id&&!state.aiJobId){
    renderAiAnalysis(snapshot.latest_ai_analysis);
    syncAnalysisControls(snapshot.latest_ai_analysis);
  }
  void monitorVirtualAutomation(snapshot);
}
function renderMarketWatch(snapshot){
  const cards=snapshot?.market_watch||[],keywords=snapshot?.attention_keywords||[];
  $('market-watch-strip').innerHTML=cards.map(card=>{
    const value=card.value===null||card.value===undefined?'미제공':typeof card.value==='number'?`${signed(card.value,2)}${card.unit||''}`:`${esc(card.value)}${card.unit||''}`;
    const tone=typeof card.value==='number'?(card.value>0?'positive':card.value<0?'negative':''):String(card.value||'').includes('상승')?'positive':String(card.value||'').includes('하락')?'negative':'';
    return`<div><span>${esc(card.label)}</span><strong class="${tone}">${value}</strong></div>`
  }).join('')+`<small>${keywords.length?`주목: ${esc(keywords.join(' · '))}`:'동종주 가격·거래량 확인 대기'}</small>`;
}
const verificationLabel={
  VERIFIED_PRIMARY:'원출처 확인',VERIFIED_MULTI_SOURCE:'독립 2곳 본문 확인',
  PARTIALLY_VERIFIED:'부분 확인(본문 부족)',SINGLE_SOURCE:'단일 원출처',
  CONFLICTED:'핵심 사실 충돌',RETRACTED:'정정·철회',UNVERIFIED:'미검증'
};
const actionLabel={
  STRONG_BUY_REVIEW:'강한 매수 검토',BUY_REVIEW:'매수 검토',WATCH:'관찰',
  WAIT:'확인 대기',SELL_REVIEW:'매도 검토',NO_TRADE:'거래 제외'
};
function boolMark(value,good='확인',bad='미확인'){
  if(value===true)return`<span class="news-state positive">● ${good}</span>`;
  if(value===false)return`<span class="news-state negative">● ${bad}</span>`;
  return'<span class="news-state unknown">● 미제공·지연</span>';
}
function verificationTone(status){
  if(status==='VERIFIED_PRIMARY'||status==='VERIFIED_MULTI_SOURCE')return'positive';
  if(status==='CONFLICTED')return'warning';
  if(status==='RETRACTED')return'negative';
  return'unknown';
}
function sentimentTone(sentiment){
  if(String(sentiment).includes('POSITIVE'))return'positive';
  if(String(sentiment).includes('NEGATIVE'))return'negative';
  if(sentiment==='MIXED')return'warning';
  return'';
}
function timeText(value){
  if(!value)return'-';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?'-':date.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
}
function providerName(id){
  return({dart_rss:'DART 공식 RSS',open_dart:'OpenDART',open_dart_news:'OpenDART',naver_finance_news:'네이버 금융 무키뉴스',naver_news:'네이버 검색 API',manual_news:'수동 원문',manual_news_json:'수동 원문',fixture_news:'검증용 fixture',krx_news:'KRX',krx_disclosure:'KRX',company_ir:'기업 IR',gdelt:'GDELT',gdelt_news:'GDELT'})[id]||id||'-';
}
function renderCandidateCard(item){
  const v=item.verification||{},m=item.materiality||{},r=item.price_volume_reaction||{},f=item.flow_confirmation||{};
  const actionTone=item.eligible?'positive':item.action==='SELL_REVIEW'?'negative':item.action==='WATCH'||item.action==='WAIT'?'warning':'unknown';
  return `<article class="candidate-card ${actionTone}" data-news-cluster="${esc(item.cluster_id)}">
    <div class="candidate-head"><div><strong>${esc(item.name||item.symbol)} · ${esc(actionLabel[item.action]||item.action)}</strong><small>${timeText(item.news_time)}</small></div><b>${fmt(item.candidate_score,0)}점</b></div>
    <h4>${esc(item.news_title)}</h4>
    <div class="candidate-score-label">${esc(item.score_label||'현재 뉴스·수급·가격 확인 점수')} · 통계·백테스트 수치 아님</div>
    <div class="candidate-pillars">
      <div><span>1. 뉴스 사실 검증</span><strong class="${verificationTone(v.status)}">${esc(verificationLabel[v.status]||v.status||'미검증')}</strong><small>독립 ${fmt(v.independent_source_count)} · 1차 ${fmt(v.primary_source_count)} · 재배포 ${fmt(v.syndicated_copy_count)}</small></div>
      <div><span>2. 중요도·방향</span><strong class="${sentimentTone(item.sentiment?.sentiment)}">${fmt(m.score)} · ${esc(item.sentiment?.sentiment||'NEUTRAL')}</strong><small>${esc(m.label||'')}</small></div>
      <div><span>3. 가격·거래량 반응</span><strong>${boolMark(r.price_confirmation,'가격 확인','가격 역행')} ${boolMark(r.volume_confirmation,'거래량 확인','거래량 미확인')}</strong><small>뉴스 발표 뒤 데이터만 사용</small></div>
      <div><span>4. 외국인·기관·회원사</span><strong>${boolMark(f.institution_confirmation,'기관 순매수','기관 순매도')} ${boolMark(f.foreign_confirmation,'외국인 순매수','외국인 순매도')} ${boolMark(f.member_confirmation,'회원사 매수창구','회원사 매도창구')}</strong><small>기관 투자자와 증권사 회원사 창구는 별개</small></div>
    </div>
    ${item.blocking_reasons?.length?`<div class="candidate-block">등록 차단: ${item.blocking_reasons.map(esc).join(' · ')}</div>`:''}
  </article>`;
}
function flowValue(value){
  const number=Number(value);
  return value===null||value===undefined||!Number.isFinite(number)
    ?'<strong class="unknown">미제공·지연</strong>'
    :`<strong class="${number>0?'positive':number<0?'negative':''}">${fmtMoney(number)}</strong>`;
}
function renderInstitutionPriority(cluster){
  const f=cluster?.evaluation?.flow_confirmation||{};
  $('institution-priority-strip').innerHTML=`<div><span>기관 투자자</span>${flowValue(f.institution_current_net)}<small>${esc(f.institution_note||'기관 장중 누적 수급 · 뉴스 후 증분과 구분')}</small></div>
    <div><span>외국인</span>${flowValue(f.foreign_current_net)}<small>외국인 장중 누적 수급</small></div>
    <div><span>증권사 회원사 창구</span><strong>${boolMark(f.member_confirmation,'매수창구 우세','매도창구 우세')}</strong><small>${esc(f.member_note||'기관 투자자와 별개')}</small></div>`;
}
function renderNewsDetail(cluster){
  state.selectedNewsCluster=cluster?.cluster_id||null;
  renderInstitutionPriority(cluster);
  if(!cluster){$('news-detail').innerHTML='<div class="analysis-empty">뉴스 사건 클러스터를 선택하십시오.</div>';return}
  const e=cluster.evaluation||{},v=e.verification||cluster.verification||{},m=e.materiality||cluster.materiality||{},s=e.sentiment||cluster.sentiment||{},r=e.price_volume_reaction||{},f=e.flow_confirmation||{};
  const articles=cluster.articles||[],facts=cluster.facts||[],contradictions=cluster.contradictions||[];
  const articleRows=articles.map(a=>`<a class="news-source-row" href="${esc(a.url||'#')}" target="_blank" rel="noopener"><span>${esc(a.publisher||a.publisher_domain||a.provider_id)}</span><b>${a.is_disclosure?'1차 공시':a.original_publisher?`원출처 ${esc(a.original_publisher)}`:a.body?'본문 수집':'요약만'}</b></a>`).join('');
  $('news-detail').innerHTML=`<div class="news-detail-head"><div><strong>${esc(cluster.canonical_title)}</strong><small>${timeText(cluster.first_published_at)} · 사건 ${esc(cluster.event_type||'OTHER')}</small></div><span class="${verificationTone(v.status)}">${esc(verificationLabel[v.status]||v.status)}</span></div>
    <section class="news-pillar"><h4>1. 뉴스 사실 검증</h4><div class="news-metrics"><span>사건 클러스터 <b>1건</b></span><span>독립 원출처 <b>${fmt(v.independent_source_count)}</b></span><span>1차 공식 원출처 <b>${fmt(v.primary_source_count)}</b></span><span>재배포 묶음 <b>${fmt(v.syndicated_copy_count)}</b></span><span>본문 수집 <b>${fmt(v.body_collected_count)}</b></span></div><p>${esc(v.reason||'검증 근거 없음')} · 검증 수는 기사 개수가 아니라 독립 원출처와 1차 공식 출처 기준입니다. 원시 수집 ${fmt(v.article_count)}건은 감사용 참고값입니다.</p>${facts.length?`<div class="fact-list">${facts.map(x=>`<span>${esc(x.fact_type||'FACT')}${x.amount?` · ${fmtMoney(x.amount)}`:''}</span>`).join('')}</div>`:''}${contradictions.length?`<div class="news-conflict">${contradictions.map(x=>esc(x.message)).join(' · ')}</div>`:''}<div class="news-source-list">${articleRows||'<span class="unknown">원출처 링크 미제공</span>'}</div></section>
    <section class="news-pillar"><h4>2. 뉴스 중요도와 방향</h4><div class="news-direction"><strong>${fmt(m.score)}점 · ${esc(m.label||'-')}</strong><b class="${sentimentTone(s.sentiment)}">${esc(s.sentiment||'NEUTRAL')}</b></div><p>${(m.limitations||[]).map(esc).join(' · ')||'규칙 기반 중요도와 방향입니다.'}</p></section>
    <section class="news-pillar"><h4>3. 가격·거래량 반응</h4><div class="news-confirm-grid">${boolMark(r.price_confirmation,'가격 확인','가격 역행')}${boolMark(r.volume_confirmation,'거래량 확인','거래량 미확인')}<span>1분 ${r.return_after_news_1m==null?'<i class="unknown">대기</i>':`<b class="${r.return_after_news_1m>=0?'positive':'negative'}">${signed(r.return_after_news_1m,2)}%</b>`}</span><span>3분 ${r.return_after_news_3m==null?'<i class="unknown">대기</i>':`<b class="${r.return_after_news_3m>=0?'positive':'negative'}">${signed(r.return_after_news_3m,2)}%</b>`}</span><span>5분 ${r.return_after_news_5m==null?'<i class="unknown">대기</i>':`<b class="${r.return_after_news_5m>=0?'positive':'negative'}">${signed(r.return_after_news_5m,2)}%</b>`}</span><span>거래량 배수 ${r.volume_ratio_after_news==null?'<i class="unknown">미제공</i>':`<b>${fmt(r.volume_ratio_after_news,2)}배</b>`}</span></div><p>기준시각 ${timeText(r.basis_time)} 이후 관측치만 계산 · 미래 데이터 사용 ${r.future_data_used?'오류':'없음'}</p></section>
    <section class="news-pillar institution-first"><h4>4. 외국인·기관·회원사 수급 확인</h4><div class="flow-separate"><div><span>기관 투자자 누적 순매수</span><strong class="${f.institution_current_net>0?'positive':f.institution_current_net<0?'negative':'unknown'}">${f.institution_current_net==null?'미제공':fmtMoney(f.institution_current_net)}</strong><small>${esc(f.institution_note||'')}</small></div><div><span>외국인 누적 순매수</span><strong class="${f.foreign_current_net>0?'positive':f.foreign_current_net<0?'negative':'unknown'}">${f.foreign_current_net==null?'미제공':fmtMoney(f.foreign_current_net)}</strong><small>뉴스 이후 증분은 별도 시계열 없으면 생성하지 않음</small></div><div><span>증권사 회원사 창구</span><strong>${boolMark(f.member_confirmation,'매수창구 우세','매도창구 우세')}</strong><small>${esc(f.member_note||'기관 투자자 분류가 아님')}</small></div></div></section>`;
}
function renderNewsOverview(overview){
  state.newsOverview=overview||{clusters:[],buy_review:[],attention:[],provider_statuses:[],promoted_features:[]};
  $('news-refresh-time').textContent=overview?.last_refresh?`갱신 ${timeText(overview.last_refresh)}`:'갱신 전';
  $('news-provider-line').innerHTML=(overview?.provider_statuses||[]).map(p=>`<span class="provider-pill ${p.available?'positive':'unknown'}" title="${esc(p.message||'')}">${esc(providerName(p.provider_id))} · ${esc(p.state||'')}</span>`).join('')||'<span class="unknown">연결된 뉴스 공급자 없음</span>';
  const clusters=overview?.clusters||[];
  $('news-list').innerHTML=clusters.length?clusters.map(c=>{const e=c.evaluation||{},v=e.verification||c.verification||{},r=e.price_volume_reaction||{},f=e.flow_confirmation||{};return`<button class="news-row ${state.selectedNewsCluster===c.cluster_id?'active':''}" data-news-cluster="${esc(c.cluster_id)}"><span class="news-row-time">${timeText(c.first_published_at)}</span><strong>${esc(c.canonical_title)}</strong><small><b class="${verificationTone(v.status)}">${esc(verificationLabel[v.status]||v.status)}</b> · 사건 1 / 독립 원출처 ${fmt(v.independent_source_count)} / 1차 ${fmt(v.primary_source_count)} / 재배포 ${fmt(v.syndicated_copy_count)} · 중요도 ${fmt(e.materiality?.score||c.materiality?.score)}</small><span class="news-row-checks">${boolMark(r.price_confirmation,'가격','가격×')}${boolMark(r.volume_confirmation,'거래량','거래량×')}${boolMark(f.institution_confirmation,'기관','기관×')}${boolMark(f.member_confirmation,'회원사','회원사×')}</span></button>`}).join(''):'<div class="analysis-empty">최근 24시간에 이 종목과 연결된 무키 종목뉴스·DART 공시 사건이 없습니다.</div>';
  const selected=clusters.find(c=>c.cluster_id===state.selectedNewsCluster)||clusters[0]||null;
  renderNewsDetail(selected);
  $('buy-review-list').innerHTML=(overview?.buy_review||[]).map(renderCandidateCard).join('')||'<div class="analysis-empty">등록 조건을 모두 통과한 매수 검토 종목이 없습니다.<br>검증 뉴스 + 가격·거래량·수급 중 2개 독립 그룹이 필요합니다.</div>';
  $('attention-list').innerHTML=(overview?.attention||[]).map(renderCandidateCard).join('')||'<div class="analysis-empty">현재 주의·대기 항목이 없습니다.</div>';
  $('candidate-strip-items').innerHTML=(overview?.buy_review||[]).map(item=>`<button data-news-cluster="${esc(item.cluster_id)}"><strong>${esc(item.name)}</strong><span>${esc(actionLabel[item.action])} · ${fmt(item.candidate_score)}점</span></button>`).join('')||'<span class="unknown">검증·가격·수급 조건을 통과한 후보 없음</span>';
  document.querySelectorAll('[data-news-cluster]').forEach(el=>el.onclick=()=>{const cluster=(state.newsOverview?.clusters||[]).find(c=>c.cluster_id===el.dataset.newsCluster);if(cluster){state.selectedNewsCluster=cluster.cluster_id;activateRightTab('news');renderNewsOverview(state.newsOverview)}});
  if(state.snapshot)renderFeatures(state.snapshot);
}
function renderAiLiveContext(){
  if(!$('ai-live-context'))return;
  const clusters=state.newsOverview?.clusters||[],verified=clusters.filter(c=>['VERIFIED_PRIMARY','VERIFIED_MULTI_SOURCE'].includes(c.verification?.status)).length;
  const features=(state.snapshot?.features||[]).filter(f=>f.value!==null&&f.value!==undefined).length;
  const mode=state.snapshot?.mode==='kis'?'KIS 실제 시세':state.snapshot?.mode==='public'?'간편 무키 실데이터':'DEMO';
  const naver=(state.newsOverview?.provider_statuses||[]).find(p=>p.provider_id==='naver_finance_news'||p.provider_id==='naver_news');
  const name=state.snapshot?.symbol_profile?.name||state.stock?.name||state.snapshot?.symbol_name||state.symbol;
  $('ai-live-context').innerHTML=`${mode} · 피처 <b>${fmt(features)}</b>개 · 뉴스 사건 <b>${fmt(clusters.length)}</b>개(검증 ${fmt(verified)}) · 네이버 <b class="${naver?.available?'positive':'unknown'}">${esc(naver?.state||'미연결')}</b><br>GPT 입력: ${esc(name)}(${esc(state.symbol)}) · KST 기준시각 · 전체 피처 목록·수급·독립 원출처 뉴스 포함`;
}
async function refreshNews(){
  const btn=$('news-refresh-btn');btn.disabled=true;btn.textContent='검증 중…';
  try{const out=await api(`/api/news/refresh?symbol=${encodeURIComponent(state.symbol)}`,{method:'POST'});renderNewsOverview(out.overview||out);if(out.ai_job_id&&!state.aiJobId){state.aiJobId=out.ai_job_id;state.aiPoll=setInterval(pollAiJob,550);await pollAiJob()}toast('네이버 무키 종목뉴스·DART 공시 원출처를 갱신하고 GPT 분석에 전달했습니다.')}
  catch(e){toast(`뉴스 갱신 실패: ${e.message}`,true)}
  finally{btn.disabled=false;btn.textContent='뉴스 새로고침'}
}
function radarCard(label,value,unit='',tone=''){const formatted=value===null||value===undefined?'-':unit==='원'?fmtMoney(value):typeof value==='number'?fmt(value,Math.abs(value)<10?2:1):esc(value);return `<div class="radar-card ${tone}"><span>${label}</span><strong>${formatted}${unit==='원'?'':unit}</strong></div>`}
function renderRadar(f={}){
  const foreign=Number(f.foreign_net||0),inst=Number(f.institution_net||0),decline=Number(f.decline_ratio||0),vix=Number(f.vix||0),short=Number(f.short_ratio||0),ret5=Number(f.ret_5||0);
  $('radar-cards').innerHTML=[
    radarCard('5분 추세',signed(ret5,2),'%',ret5>=0?'positive':'negative'),
    radarCard('외국인 순매수',foreign,'원',foreign>=0?'positive':'negative'),
    radarCard('기관 순매수',inst,'원',inst>=0?'positive':'negative'),
    radarCard('하락 breadth',fmt(decline,1),'%',decline>65?'negative':decline<40?'positive':''),
    radarCard('VIX',fmt(vix,1),'',vix>25?'negative':''),
    radarCard('공매도 비중',fmt(short,2),'%',short>8?'negative':'')
  ].join('');
}
function renderPortfolio(p={}){
  state.portfolio=p;
  $('summary-seed').textContent=fmtMoney(p.seed_money);
  $('summary-cash').textContent=fmtMoney(p.cash);
  $('summary-pnl').textContent=`${Number(p.realized_pnl)>=0?'+':'-'}${fmtMoney(Math.abs(p.realized_pnl||0))}`;
  $('summary-pnl').className=Number(p.realized_pnl)>=0?'positive':'negative';
  $('summary-closed-count').textContent=`${fmt(state.trades.filter(t=>t.status==='CLOSED').length)}건`;
  const open=state.trades.filter(t=>t.status==='OPEN');
  $('open-position-table').innerHTML=open.length?open.map(t=>`<tr data-trade-id="${t.id}" class="clickable"><td>${esc(t.symbol_name)} <b class="${t.side==='SHORT'?'negative':'positive'}">${esc(t.side||'LONG')}</b></td><td>${fmt(t.entry_quantity)}</td><td>${fmtMoney(t.entry_price)}</td><td><span class="badge-open">OPEN</span></td><td><button class="row-close" data-close="${t.id}">청산</button></td></tr>`).join(''):'<tr><td colspan="5" class="empty-cell">보유 포지션 없음</td></tr>';
  const closed=state.trades.filter(t=>t.status==='CLOSED').slice(0,16);
  $('recent-trade-table').innerHTML=closed.length?closed.map(t=>`<tr data-trade-id="${t.id}" class="clickable ${state.selectedTradeId===t.id?'selected':''}"><td>${new Date(t.entry_time).toLocaleString('ko-KR',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})} ${esc(t.side||'LONG')}</td><td>${fmtMoney(t.entry_price)}→${fmtMoney(t.exit_price)}</td><td class="${Number(t.return_pct)>=0?'positive':'negative'}">${signed(t.return_pct||0,3)}%</td><td>${Number(t.net_pnl)>0?'승':'패'}</td><td><button class="trade-ai-btn" data-trade-ai="${t.id}">GPT 복기</button></td></tr>`).join(''):'<tr><td colspan="5" class="empty-cell">완료 거래 없음</td></tr>';
  document.querySelectorAll('[data-trade-id]').forEach(row=>row.onclick=e=>{if(e.target.dataset.close)return;selectTrade(Number(row.dataset.tradeId))});
  document.querySelectorAll('[data-close]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();state.selectedTradeId=Number(btn.dataset.close);closeSelectedPosition()});
  document.querySelectorAll('[data-trade-ai]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();startTradeAiDebrief(Number(btn.dataset.tradeAi))});
}
function renderTrades(trades=[]){
  state.trades=trades;
  $('trade-log-table').innerHTML=trades.length?trades.map(t=>`<tr class="clickable ${state.selectedTradeId===t.id?'selected':''}" data-log-id="${t.id}"><td>${t.id}</td><td>${t.mode==='historical'?'과거':t.mode==='virtual-auto-gpt'?'GPT자동모의':'가상'} · ${esc(t.side||'LONG')} · ${t.status}</td><td>${new Date(t.entry_time).toLocaleString('ko-KR')}<br><span class="muted">${fmtMoney(t.entry_price)}</span></td><td>${t.exit_time?`${new Date(t.exit_time).toLocaleString('ko-KR')}<br><span class="muted">${fmtMoney(t.exit_price)}</span>`:'-'}</td><td>${fmt(t.entry_quantity)}</td><td class="${Number(t.net_pnl||0)>=0?'positive':'negative'}">${t.status==='CLOSED'?`${Number(t.net_pnl)>=0?'+':'-'}${fmtMoney(Math.abs(t.net_pnl||0))}`:'OPEN'}</td><td>${t.status==='CLOSED'?`<button class="trade-ai-btn" data-trade-ai="${t.id}">GPT 복기</button>`:'-'}</td></tr>`).join(''):'<tr><td colspan="7" class="empty-cell">거래 로그 없음</td></tr>';
  document.querySelectorAll('[data-log-id]').forEach(row=>row.onclick=e=>{if(!e.target.dataset.tradeAi)selectTrade(Number(row.dataset.logId))});
  document.querySelectorAll('[data-trade-ai]').forEach(btn=>btn.onclick=e=>{e.stopPropagation();startTradeAiDebrief(Number(btn.dataset.tradeAi))});
  renderChart(true);
  renderPortfolio(state.portfolio||{});
}
async function refreshTrades(){
  const [trades,p]=await Promise.all([api('/api/trades'),api('/api/portfolio')]);
  state.portfolio=p;renderTrades(trades);renderPortfolio(p);
}
async function selectTrade(id){
  state.selectedTradeId=id;renderTrades(state.trades);
  try{const a=await api(`/api/trades/${id}/analysis`);renderAnalysis(a);activateRightTab('analysis')}catch(e){toast(e.message,true)}
}
async function startTradeAiDebrief(id){
  const trade=state.trades.find(row=>row.id===id);if(!trade||trade.status!=='CLOSED')return toast('청산 완료 거래만 GPT 복기가 가능합니다.',true);
  state.selectedTradeId=id;activateRightTab('features');autoFillPrompt('POSITION',id);await requestAiAnalysis('POSITION',id);
}
function renderAnalysis(a){
  $('analysis-empty').classList.add('hidden');$('analysis-result').classList.remove('hidden');
  const positive=a.result==='승리';
  const worked=a.what_worked||[],failed=a.what_failed||[],changes=a.feature_changes||[];
  $('analysis-result').innerHTML=`<div class="analysis-head ${positive?'positive':'negative'}"><strong>${esc(a.result)} · ${esc(a.side||'LONG')} 모의거래</strong><span>${signed(a.net_pnl||0,0)}원 · ${signed(a.return_pct||0,3)}%</span></div>
    <div class="analysis-summary">${esc(a.summary||'')}</div>
    <div class="analysis-actions"><button id="analysis-gpt-debrief">GPT 거래 복기</button><button id="analysis-back-features">실시간 피처로 돌아가기</button></div>
    ${worked.length?`<div class="section-title">성공한 이유</div>${worked.map(x=>`<article class="reason-card"><p>${esc(x)}</p></article>`).join('')}`:''}
    ${failed.length?`<div class="section-title">실패한 이유</div>${failed.map(x=>`<article class="reason-card warning"><p>${esc(x)}</p></article>`).join('')}`:''}
    <div class="section-title">진입·청산 평가</div><article class="reason-card"><strong>진입</strong><p>${esc(a.entry_assessment||'저장된 진입 피처가 부족합니다.')}</p></article><article class="reason-card"><strong>청산</strong><p>${esc(a.exit_assessment||'저장된 청산 피처가 부족합니다.')}</p></article>
    ${changes.length?`<div class="section-title">진입 → 청산 피처 변화</div><div class="feature-change-grid">${changes.map(x=>`<div><span>${esc(x.label)}</span><small>${fmt(x.entry,3)} → ${fmt(x.exit,3)} (${signed(x.change,3)})</small></div>`).join('')}</div>`:''}
    ${(a.next_time_checklist||[]).length?conditionBlock('다음 거래 체크리스트',a.next_time_checklist):''}
    <div class="section-title">규칙 기반 핵심 원인</div>${(a.reasons||[]).map(r=>`<article class="reason-card"><strong>${esc(r.title)}</strong><p>${esc(r.detail)}</p></article>`).join('')}
    ${(a.warnings||[]).length?`<div class="section-title">청산 평가</div>${a.warnings.map(r=>`<article class="reason-card warning"><strong>${esc(r.title)}</strong><p>${esc(r.detail)}</p></article>`).join('')}`:''}
    <div class="candidate-score-label">승률·기대수익·표본 수를 생성하지 않는 단일 거래 복기입니다.</div>`;
  $('analysis-gpt-debrief').onclick=()=>requestAiAnalysis('POSITION',state.selectedTradeId);
  $('analysis-back-features').onclick=()=>activateRightTab('features');
}
function renderAlertBanner(snapshot){
  const matched=(snapshot?.active_alerts||[]).filter(a=>{const v=Number(snapshot?.flat?.[a.feature_id]);return alertMatches(a,v)});
  const triggered=snapshot?.triggered_alerts||[],rawRows=triggered.length?triggered:matched,rows=[...new Map(rawRows.map(a=>[a.feature_id,a])).values()],banner=$('ai-alert-banner');
  if(!rows.length){banner.classList.add('hidden');banner.textContent='';return}
  const buyRows=rows.filter(a=>a.severity==='POSITIVE'),sellRows=rows.filter(a=>['DANGER','INVALIDATION'].includes(a.severity));
  const label=buyRows.length?`매수 검토 알람 ${buyRows.length}`:sellRows.length?`매도·청산 검토 알람 ${sellRows.length}`:`주의 알람 ${rows.length}`;
  banner.classList.remove('hidden');banner.textContent=`${label} 충족 · ${rows.slice(0,4).map(a=>a.feature_id).join(', ')} · 실주문 없음`;
  for(const alert of triggered){
    const key=`${alert.alert_id}:${alert.triggered_at||alert.current_value}`;
    if(state.alertNotified.has(key))continue;
    state.alertNotified.add(key);
    const message=`${alert.severity==='POSITIVE'?'매수':alert.severity==='DANGER'||alert.severity==='INVALIDATION'?'매도·청산':'주의'} 검토 · ${alert.feature_id} ${fmt(alert.current_value,3)}`;
    toast(message,alert.severity!=='POSITIVE');
    if('Notification'in window&&Notification.permission==='granted')new Notification(`KR 터미널 ${state.symbol}`,{body:`${message}\n실주문 없음 · 가상대응만 가능`});
  }
}
function setAiStatus(message,progress=0,stateLabel='대기'){
  $('ai-status').textContent=message;$('ai-progress-bar').style.width=`${Math.max(0,Math.min(100,progress))}%`;$('ai-state').textContent=stateLabel;
}
function aiTone(direction){return direction==='POSITIVE'?'positive':direction==='NEGATIVE'?'negative':direction==='CAUTION'||direction==='CONFLICTED'?'warning':'unknown'}
function conditionBlock(label,items=[]){return items.length?`<div class="ai-condition"><strong>${esc(label)}</strong><ul>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`:''}
const operatorLabel={GT:'초과',GTE:'이상',LT:'미만',LTE:'이하',BETWEEN:'범위',NONE:'미제공'};
function thresholdText(threshold={}){
  if(!threshold||threshold.operator==='NONE'||threshold.value1===null||threshold.value1===undefined)return'미제공';
  const second=threshold.operator==='BETWEEN'?` ~ ${fmt(threshold.value2,3)}`:'';
  return`${fmt(threshold.value1,3)}${second} ${operatorLabel[threshold.operator]||threshold.operator}`;
}
function renderTradePlan(plan={}){
  const side=plan.side||'NONE',sideTone=side==='LONG'?'positive':side==='SHORT'?'negative':'warning';
  const entryLow=plan.entry_price_low_krw,entryHigh=plan.entry_price_high_krw;
  const entry=entryLow||entryHigh?`${fmtMoney(entryLow||entryHigh)}${entryLow&&entryHigh?` ~ ${fmtMoney(entryHigh)}`:''}`:'진입 없음';
  const stop=plan.stop_loss_price_krw?`${fmtMoney(plan.stop_loss_price_krw)} (${signed(plan.stop_loss_pct||0,2)}%)`:'미제공';
  const target1=plan.take_profit_1_price_krw?`${fmtMoney(plan.take_profit_1_price_krw)} (+${fmt(Math.abs(plan.take_profit_1_pct||0),2)}%)`:'미제공';
  const target2=plan.take_profit_2_price_krw?`${fmtMoney(plan.take_profit_2_price_krw)} (+${fmt(Math.abs(plan.take_profit_2_pct||0),2)}%)`:'미제공';
  return`<section class="trade-plan"><div class="trade-plan-head"><strong class="${sideTone}">${esc(plan.plan_label||'조건부 거래 계획')} · ${esc(side)}</strong><small>${esc(plan.entry_method||'NO_ENTRY')} · 자동주문 없음</small></div>
    <div class="trade-price-grid"><div><span>가상 진입 지정 범위</span><strong>${entry}</strong><small>${esc(plan.entry_instruction||'')}</small></div><div><span>손절 검토선</span><strong class="negative">${stop}</strong><small>진입 기준 손실률</small></div><div><span>1차 / 2차 익절 검토선</span><strong class="positive">${target1}</strong><small>${target2}${plan.trailing_stop_pct?` · 추적 ${fmt(plan.trailing_stop_pct,2)}%`:''}</small></div></div>
    ${(plan.feature_triggers||[]).length?`<div class="trade-trigger-list">${plan.feature_triggers.map(t=>`<div class="trade-trigger-row"><span><b>${esc(t.display_name)}</b><br><small>현재 ${esc(t.current_display)}</small></span><span>${fmt(t.buy_threshold,3)} ${operatorLabel[t.buy_operator]||t.buy_operator}<br><small>${esc(t.required_move)} · ${esc(t.explanation)}</small></span></div>`).join('')}</div>`:''}
    ${(plan.no_trade_conditions||[]).length?conditionBlock('진입 금지·무효화 조건',plan.no_trade_conditions):''}<div class="trade-plan-note">${esc(plan.risk_notice||'가상거래·수동 검토 전용')}</div></section>`;
}
function renderAiDebrief(debrief={}){
  if(!debrief.applicable)return'';
  return`<section class="ai-debrief"><h4>GPT 거래 복기 · #${fmt(debrief.trade_id)}</h4><p>${esc(debrief.execution_summary||'')}</p><div class="ai-debrief-grid"><div><strong class="positive">성공 요인</strong>${(debrief.what_worked||[]).length?`<ul>${debrief.what_worked.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<small>해당 없음</small>'}</div><div><strong class="negative">실패 요인</strong>${(debrief.what_failed||[]).length?`<ul>${debrief.what_failed.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<small>해당 없음</small>'}</div></div><p><b>진입:</b> ${esc(debrief.entry_assessment||'')}<br><b>청산:</b> ${esc(debrief.exit_assessment||'')}</p>${conditionBlock('다음 거래 체크',debrief.next_time_checklist||[])}</section>`;
}
function renderAiAnalysis(a){
  const changed=state.aiAnalysis?.analysis_id&&state.aiAnalysis.analysis_id!==a?.analysis_id;
  state.aiAnalysis=a;if(!a)return;
  if(changed){state.semiAutoPlaybook=null;$('semi-auto-playbook').classList.add('hidden');$('semi-auto-playbook').innerHTML=''}
  const expired=new Date(a.expires_at)<new Date(),stat=a.historical_statistics||{},top=a.top_features||[];
  $('ai-answer').innerHTML=`<div class="ai-result-head">
    <div class="ai-metric"><span>시장 체제</span><strong>${esc(a.market_regime?.label||'-')}</strong><small>${fmt((a.market_regime?.confidence||0)*100,0)}%</small></div>
    <div class="ai-metric"><span>종목 상태</span><strong>${esc(a.symbol_state?.label||'-')}</strong><small>${fmt((a.symbol_state?.confidence||0)*100,0)}%</small></div>
    <div class="ai-metric"><span>종합 의견</span><strong class="${aiTone(a.overall_action?.code?.includes('SELL')||a.overall_action?.code?.includes('REDUCE')?'NEGATIVE':a.overall_action?.code?.includes('BUY')?'POSITIVE':'NEUTRAL')}">${esc(a.overall_action?.korean_label||'-')}</strong><small>${fmt((a.overall_action?.confidence||0)*100,0)}%</small></div>
    <div class="ai-metric"><span>데이터 품질 · 만료</span><strong>${fmt(a.data_quality?.score||0)}점</strong><small class="${expired?'warning':''}">${expired?'만료됨':new Date(a.expires_at).toLocaleTimeString('ko-KR',{hour12:false})}</small></div>
  </div>
  <div class="ai-human-answer">${esc(a.human_readable_summary||'')}</div>
  ${renderTradePlan(a.conditional_trade_plan||{})}
  ${renderAiDebrief(a.trade_debrief||{})}
  ${conditionBlock('매수 검토 조건',a.buy_review_conditions)}${conditionBlock('보유 조건',a.hold_conditions)}${conditionBlock('주의 조건',a.caution_conditions)}
  ${conditionBlock('비중 축소 검토',a.reduce_review_conditions)}${conditionBlock('매도 검토 조건',a.sell_review_conditions)}${conditionBlock('판단 무효화',a.invalidation_conditions)}
  <div class="ai-top-list"><div class="section-title">해당 종목 트리거 피처 · 매수/매도 기준선 1~12위</div>${top.map(f=>`<div class="ai-top-row" title="${esc(f.reason)}"><b>${f.rank}</b><span class="${aiTone(f.direction_now)}">${esc(f.display_name)}<small>${esc(f.reason)}</small></span><strong>${esc(f.current_display)}</strong><div class="ai-feature-thresholds"><span>매수·긍정 기준<em>${thresholdText(f.thresholds?.positive)}</em></span><span>매도·위험 기준<em>${thresholdText(f.thresholds?.danger)}</em></span><span>주의 기준<em>${thresholdText(f.thresholds?.caution)}</em></span><span>무효화 기준<em>${thresholdText(f.thresholds?.invalidation)}</em></span></div></div>`).join('')}</div>
  ${top.some(f=>Object.values(f.thresholds||{}).some(t=>t.basis==='AI_HEURISTIC'))?'<div class="ai-threshold-note">AI 추정 경계값 · 백테스트 검증 전</div>':''}
  <div class="ai-condition"><strong>통계 엔진</strong>${stat.available?'연결됨':'미연결 · 승률/기대수익 표시 안 함'}</div>
  ${conditionBlock('한계',a.limitations||[])}`;
  $('ai-state').textContent=expired?'만료':'완료';
  renderFeatures(state.snapshot);
}
function promptFeatureValue(feature){
  const value=feature?.value;if(value===null||value===undefined||value==='')return'미제공';
  if(typeof value==='number')return`${fmt(value,Math.abs(value)<10?3:1)}${feature.unit||''}`;
  return`${String(value)}${feature.unit||''}`;
}
const MAX_AI_PROMPT_LENGTH=50000;
function promptText(value,max=80){
  const text=String(value??'').replace(/\s+/g,' ').trim();
  return text.length>max?`${text.slice(0,Math.max(0,max-1))}…`:text;
}
function promptKstTime(value=new Date()){
  const date=value instanceof Date?value:new Date(value),formatter=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
  if(Number.isNaN(date.getTime()))return'미수집';
  const parts=Object.fromEntries(formatter.formatToParts(date).filter(x=>x.type!=='literal').map(x=>[x.type,x.value]));
  return`${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second} KST (UTC+09:00)`;
}
function promptAnalysisContext(){
  const profile=state.snapshot?.symbol_profile||{},stock=state.stock||{},name=profile.name||stock.name||state.snapshot?.symbol_name||state.symbol;
  const market=profile.market||stock.market||'시장 미확인',sector=profile.sector||stock.sector||'업종 미확인',industry=profile.industry||'';
  return`[분석 대상 회사]\n회사명: ${promptText(name,80)}\n종목코드: ${state.symbol}\n시장/업종: ${promptText(market,30)} / ${promptText(sector,50)}${industry?` / ${promptText(industry,60)}`:''}\n분석 기준시각: ${promptKstTime()}\n데이터 스냅샷 수집시각: ${state.snapshot?.time?promptKstTime(state.snapshot.time):'미수집'}\n아래에 직접 적힌 데이터만 이 기준시각 현재의 증거로 사용하고, 화면의 표시 위치나 화면 상태에 의존하지 마라.`;
}
function triggerFeaturePrompt(){
  const features=state.snapshot?.features||[],byId=new Map(features.map(f=>[f.feature_id,f]));
  const aiIds=(state.aiAnalysis?.top_features||[]).map(f=>f.feature_id),promoted=[...(state.snapshot?.market_promoted_features||[]),...(state.snapshot?.news_promoted_features||[])];
  const core=['price','relative_volume_1m','vwap_distance','price_acceleration_1m','opening_range_breakout','orderbook_imbalance','foreign_net','institution_net','foreign_flow_acceleration','flow_acceleration','kospi_change','kosdaq_change','kospi200_futures_change','night_futures_change','peer_attack_score','peer_up_ratio','news_verification_status','news_materiality_score'];
  const ids=[...new Set([...aiIds,...promoted,...core,...features.filter(f=>f.value!==null&&f.value!==undefined).map(f=>f.feature_id),...features.map(f=>f.feature_id)])];
  const ordered=ids.map(id=>byId.get(id)).filter(Boolean),available=ordered.filter(f=>f.value!==null&&f.value!==undefined),missing=ordered.length-available.length;
  const detailRows=available.slice(0,20).map((f,index)=>{
    const ai=(state.aiAnalysis?.top_features||[]).find(x=>x.feature_id===f.feature_id),threshold=ai?` 기존 GPT 경계: 긍정 ${thresholdText(ai.thresholds?.positive)}, 위험 ${thresholdText(ai.thresholds?.danger)}, 주의 ${thresholdText(ai.thresholds?.caution)}.`:'';
    const meaning=f.description&&f.description!==f.display_name?f.description:`${f.group||'실시간'} 그룹의 ${f.display_name}`;
    return`${index+1}) ${promptText(f.display_name,50)}(${promptText(f.feature_id,60)}) 현재 ${promptText(promptFeatureValue(f),40)}, 방향 ${promptText(f.direction||f.status||'중립',20)}, 의미: ${promptText(meaning,120)}, 출처/품질: ${promptText(f.source||'미제공',90)} / ${promptText(f.freshness||f.quality||'미확인',40)}.${threshold}`;
  });
  let inventoryRows=ordered.map((f,index)=>{const api=f.metadata?.required_api?` | 필요 API:${promptText(f.metadata.required_api,24)}`:'';return`${index+1}) ${promptText(f.display_name,42)}(${promptText(f.feature_id,55)})=${promptText(promptFeatureValue(f),36)} | 상태:${promptText(f.direction||f.status||'UNKNOWN',18)} | 출처:${promptText(f.source||f.source_type||'미제공',60)} | 신선도:${promptText(f.freshness||f.quality||'미확인',24)}${api}`});
  if(inventoryRows.join('\n').length>30000){
    inventoryRows=ordered.map((f,index)=>{const api=f.metadata?.required_api?`|API:${promptText(f.metadata.required_api,15)}`:'';return`${index+1})${promptText(f.display_name,28)}(${promptText(f.feature_id,38)})=${promptText(promptFeatureValue(f),24)}|${promptText(f.direction||f.status||'UNKNOWN',14)}|${promptText(f.freshness||f.quality||'미확인',16)}${api}`});
  }
  return ordered.length?`\n[핵심 트리거 피처 설명]\n${detailRows.length?detailRows.join('\n'):'현재 값이 수집된 핵심 피처 없음.'}\n\n[수집 피처 전체 목록]\n총 ${ordered.length}개 / 값 수집 ${available.length}개 / 미제공·미연결 ${missing}개. 아래 목록은 화면 노출 여부와 관계없는 전체 피처다.\n${inventoryRows.join('\n')}\n각 피처의 LONG/SHORT 발동 기준, 주의선, 무효화/매도선을 명시적 숫자와 단위로 산출하라. 미제공 피처는 추정하지 말고 필요한 API 또는 미제공 상태를 그대로 표시하라.`:'\n[수집 피처 전체 목록]\n현재 설명 가능한 수집 피처가 없으므로 값을 추정하지 마라.';
}
function tradeFeaturePrompt(trade){
  const entry=trade?.entry_features||{},exit=trade?.exit_features||{},defs=new Map((state.snapshot?.features||[]).map(f=>[f.feature_id,f]));
  const rows=[...new Set([...Object.keys(entry),...Object.keys(exit)])].map(id=>{
    const a=Number(entry[id]),b=Number(exit[id]);if(!Number.isFinite(a)||!Number.isFinite(b))return null;
    const relative=Math.abs(b-a)/Math.max(Math.abs(a),Math.abs(b),1),priority=/price|volume|vwap|foreign|institution|member|news|kospi|future|orderbook|rsi|macd|atr/i.test(id)?2:0;
    return{id,a,b,score:priority+relative,def:defs.get(id)};
  }).filter(Boolean).sort((a,b)=>b.score-a.score).slice(0,14);
  return rows.length?`\n저장된 진입→청산 핵심 피처:\n${rows.map((row,index)=>`${index+1}) ${row.def?.display_name||row.id}(${row.id}) ${fmt(row.a,3)} → ${fmt(row.b,3)}${row.def?.unit||''}; 의미: ${row.def?.description||row.def?.group||'거래 저장 피처'}`).join('\n')}`:'\n저장된 수치 피처가 부족하므로 없는 원인을 만들지 마라.';
}
function buildAutoPrompt(type='SYMBOL',tradeId=null){
  const name=state.snapshot?.symbol_profile?.name||state.stock?.name||state.snapshot?.symbol_name||state.symbol,price=Number(state.currentTick?.price||state.snapshot?.flat?.price||0);
  const cluster=(state.newsOverview?.clusters||[])[0],news=cluster?`[최신 네이버 뉴스 사건]\n제목: ${promptText(cluster.canonical_title,240)}\n검증 상태: ${cluster.verification?.status||'미검증'} / 독립 원출처: ${cluster.verification?.independent_source_count||0}곳`:'[최신 네이버 뉴스 사건]\n검증 가능한 최신 사건 없음.';
  const context=promptAnalysisContext(),features=triggerFeaturePrompt(),rules='[분석 제약]\n현재 시간 기준의 조건부 판단만 제시하라. 미제공 값을 만들지 말고 자동주문은 절대 실행·권고하지 마라. 백테스트 엔진이 없으므로 승률·기대수익·하위손실·표본 수를 생성하지 마라.';
  let request;
  if(tradeId){const trade=state.trades.find(row=>row.id===tradeId);request=`거래 #${tradeId}(${trade?.side||'LONG'})의 진입·청산 피처와 순손익 ${trade?.net_pnl==null?'미제공':fmtMoney(trade.net_pnl)}, 수익률 ${trade?.return_pct==null?'미제공':signed(trade.return_pct,3)+'%'}를 현재 시간 기준으로 복기하라. 번 거래면 어떤 피처 조합이 성공에 기여했는지, 잃은 거래면 어떤 피처를 오판·무시했는지 분리하라. 진입 판단, 청산 판단, 다음 거래 체크리스트를 피처와 수치 중심으로 작성하라.${tradeFeaturePrompt(trade)}`}
  else if(type==='MARKET')request=`현재 시간 기준으로 한국 시장과 ${name} 주식을 분석하라. KOSPI·KOSDAQ·선물·야간선물·시장폭·동종주·수급 중 주목 피처와 매수/매도 기준선을 수치로 제시하고, 현재가 ${price?fmtMoney(price):'미제공'} 기준 LONG/SHORT 가상 진입 범위·손절·1/2차 익절 가격과 퍼센트를 조건부로 제시하라.`;
  else if(type==='POSITION')request=`현재 시간 기준으로 ${name} 보유 가상 포지션의 피처를 분석하고 보유·축소·청산 기준선, 손절·익절 가격과 퍼센트를 제시하라.`;
  else request=`현재 시간 기준으로 ${name}(${state.symbol}) 현재가 ${price?fmtMoney(price):'미제공'}의 트리거 피처를 중요도 순으로 정렬하고 각 피처가 얼마만큼 움직일 때 LONG 또는 SHORT 진입 검토인지, 매도·무효화 기준은 무엇인지 수치로 제시하라. 원화 진입 지정가 범위, 손절가·손절률, 1/2차 익절가·익절률을 직관적으로 분석하라.`;
  return`${context}\n\n[분석 요청]\n${request}\n\n${news}${features}\n\n${rules}`;
}
function selfContainedPrompt(question){
  if(question.includes('[분석 대상 회사]')&&question.includes('[수집 피처 전체 목록]'))return question.slice(0,MAX_AI_PROMPT_LENGTH);
  const evidence=`${promptAnalysisContext()}${triggerFeaturePrompt()}\n\n[분석 제약]\n현재 시간 기준으로만 판단하고 미제공 값을 추정하지 마라. 자동주문은 절대 실행·권고하지 마라.`,remaining=Math.max(0,MAX_AI_PROMPT_LENGTH-evidence.length-20);
  return`${evidence}\n\n[사용자 질문]\n${question.slice(0,remaining)}`;
}
function autoFillPrompt(type='SYMBOL',tradeId=null){const prompt=buildAutoPrompt(type,tradeId);state.lastAutoPrompt=prompt;$('ai-question').value=prompt.slice(0,MAX_AI_PROMPT_LENGTH);return $('ai-question').value}
function historicalAnalogsPrompt(){
  const rows=(state.rawBars||[]).map(row=>({time:row.time,close:Number(row.close),volume:Number(row.volume)||0})).filter(row=>Number.isFinite(row.close)&&row.close>0);
  if(rows.length<50)return '\n[로컬 과거 유사 국면]\n비교 가능한 차트 이력이 부족합니다. 유사 과거를 추정하지 마세요.';
  const change=(index,span)=>index>=span?(rows[index].close/rows[index-span].close-1)*100:null;
  const volumeRatio=index=>{const start=Math.max(0,index-20),sample=rows.slice(start,index).map(row=>row.volume).filter(value=>value>0);const average=sample.reduce((sum,value)=>sum+value,0)/sample.length;return average?rows[index].volume/average:null};
  const last=rows.length-1,current={r1:change(last,1),r3:change(last,3),r5:change(last,5),r15:change(last,15),volume:volumeRatio(last)};
  if(Object.values(current).some(value=>!Number.isFinite(value)))return '\n[로컬 과거 유사 국면]\n현재 가격·거래량 피처가 부족하여 자동 비교를 생략합니다.';
  const candidates=[];
  for(let index=20;index<rows.length-16;index++){
    const values={r1:change(index,1),r3:change(index,3),r5:change(index,5),r15:change(index,15),volume:volumeRatio(index)};
    if(Object.values(values).some(value=>!Number.isFinite(value)))continue;
    const score=Math.abs(values.r1-current.r1)*3+Math.abs(values.r3-current.r3)*2+Math.abs(values.r5-current.r5)*1.5+Math.abs(values.r15-current.r15)+Math.abs(Math.log(Math.max(values.volume,.05)/Math.max(current.volume,.05)))*2;
    candidates.push({index,values,score,after5:change(index+5,5),after15:change(index+15,15)});
  }
  const chosen=[];
  for(const candidate of candidates.sort((a,b)=>a.score-b.score)){if(chosen.every(other=>Math.abs(other.index-candidate.index)>15)){chosen.push(candidate);if(chosen.length===4)break}}
  if(!chosen.length)return '\n[로컬 과거 유사 국면]\n유효한 과거 후보가 없습니다.';
  return `\n[로컬 과거 유사 국면 — 가격·거래량 피처만 사용]\n현재 서명: 1/3/5/15바 수익률 ${fmt(current.r1,3)}% / ${fmt(current.r3,3)}% / ${fmt(current.r5,3)}% / ${fmt(current.r15,3)}%, 거래량비 ${fmt(current.volume,2)}배.\n${chosen.map((row,index)=>`${index+1}) ${promptKstTime(row.time)} | 유사도 거리 ${fmt(row.score,3)} | 당시 1/3/5/15바 수익률 ${fmt(row.values.r1,3)}%/${fmt(row.values.r3,3)}%/${fmt(row.values.r5,3)}%/${fmt(row.values.r15,3)}%, 거래량비 ${fmt(row.values.volume,2)}배 | 이후 5/15바 반응 ${fmt(row.after5,3)}%/${fmt(row.after15,3)}%`).join('\n')}\n이 후보는 가격·수익률·거래량 이력으로만 산출되었습니다. 수급·뉴스·호가 등 과거 이력이 제공되지 않은 피처는 유사하다고 단정하지 마세요.`;
}
function manualJsonFormat(){return `\n[반드시 아래 JSON 하나만 반환]\n{\n  "overall_action":"BUY_REVIEW | HOLD | WAIT | SELL_REVIEW | NO_TRADE",\n  "summary":"현재 판단과 핵심 근거",\n  "historical_similarity":"현재와 가장 유사한 과거 후보 및 공통 피처/차이점",\n  "thresholds":[\n    {"feature_id":"입력 목록의 정확한 ID","buy":{"operator":"GT|GTE|LT|LTE|BETWEEN","value1":숫자,"value2":null},"sell":{"operator":"GT|GTE|LT|LTE|BETWEEN","value1":숫자,"value2":null},"reason":"기준선 근거"}\n  ]\n}\n규칙: thresholds에는 현재 숫자값이 실제로 제공된 피처만 최대 12개 사용합니다. 매수와 매도 모두 숫자 기준선을 제시할 수 있을 때만 넣고, 모르면 항목을 생략합니다. 가격 목표·손절가는 feature_id가 price일 때만 표현합니다. 자동 주문이나 수익 보장은 제안하지 마세요.`}
function buildManualChatGptPrompt(){
  const base=selfContainedPrompt(buildAutoPrompt('SYMBOL'));
  return `${base.slice(0,Math.max(0,MAX_AI_PROMPT_LENGTH-6500))}${historicalAnalogsPrompt()}\n\n[수동 ChatGPT 분석 추가 지시]\n제공된 전체 피처와 로컬 과거 유사 국면을 대조해, 현재와 가장 유사하게 반응한 피처 조합이 무엇인지 설명하세요. 유사 국면 이후 반응은 참고용이며 미래 예측 근거로 단정하지 마세요.${manualJsonFormat()}`.slice(0,MAX_AI_PROMPT_LENGTH);
}
function downloadTextFile(name,text,type='text/plain;charset=utf-8'){const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function copyText(text){
  try{await navigator.clipboard.writeText(text);return true}catch{
    const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();const copied=document.execCommand('copy');area.remove();return copied;
  }
}
async function downloadChartCapture(){
  const chart=$('main-chart');if(!chart?.data?.length||!window.Plotly?.toImage)return false;
  try{const image=await Plotly.toImage(chart,{format:'png',width:Math.max(chart.clientWidth,1100),height:Math.max(chart.clientHeight,650),scale:2});const link=document.createElement('a');link.href=image;link.download=`${state.symbol}-chart-${new Date().toISOString().replace(/[:.]/g,'-')}.png`;document.body.appendChild(link);link.click();link.remove();return true}catch(error){console.warn('chart capture failed',error);return false}
}
async function openManualChatGpt(){
  const prompt=buildManualChatGptPrompt();
  const popup=window.open('https://chatgpt.com/','_blank','noopener');
  const copied=await copyText(prompt);downloadTextFile(`${state.symbol}-chatgpt-manual-analysis.md`,prompt,'text/markdown;charset=utf-8');const captured=await downloadChartCapture();
  $('manual-gpt-status').textContent=`${copied?'프롬프트를 클립보드에 복사했습니다':'프롬프트 파일을 열어 복사하세요'} · ${captured?'차트 PNG를 다운로드했습니다':'차트 PNG 생성에 실패했습니다'} · 열린 ChatGPT에 프롬프트를 붙여넣고 PNG를 첨부하세요.`;
  if(!popup)$('manual-gpt-status').textContent+=' 팝업이 차단되었다면 chatgpt.com을 직접 여세요.';
}
function extractJsonBlock(text){const source=String(text||'').trim();const fenced=source.match(/```(?:json)?\s*([\s\S]*?)```/i);const candidate=(fenced?.[1]||source).trim();const start=candidate.indexOf('{'),end=candidate.lastIndexOf('}');if(start<0||end<=start)throw new Error('JSON 객체를 찾지 못했습니다. ChatGPT 답변의 JSON 코드 블록 전체를 붙여넣으세요.');return JSON.parse(candidate.slice(start,end+1))}
function normalizeManualRule(rule){if(!rule||typeof rule!=='object')return null;const operator=String(rule.operator||'').toUpperCase(),value1=Number(rule.value1),value2=rule.value2===null||rule.value2===undefined?null:Number(rule.value2);if(!['GT','GTE','LT','LTE','BETWEEN'].includes(operator)||!Number.isFinite(value1)||(operator==='BETWEEN'&&!Number.isFinite(value2)))return null;return{operator,value1,value2:operator==='BETWEEN'?value2:null}}
function applyManualChatGptResponse(){
  try{
    const answer=extractJsonBlock($('manual-gpt-response').value),features=new Map((state.snapshot?.features||[]).map(feature=>[feature.feature_id,feature])),accepted={};
    for(const item of Array.isArray(answer.thresholds)?answer.thresholds:[]){const id=String(item?.feature_id||''),feature=features.get(id),buy=normalizeManualRule(item?.buy),sell=normalizeManualRule(item?.sell);if(!feature||!Number.isFinite(Number(feature.value))||!buy||!sell)continue;accepted[id]={buy,sell,reason:String(item.reason||'').slice(0,240)};}
    if(!Object.keys(accepted).length)throw new Error('현재 숫자 피처와 일치하는 매수·매도 기준선이 없습니다. 프롬프트에 나온 feature_id와 지정 JSON 형식을 확인하세요.');
    state.manualThresholds=accepted;localStorage.setItem(manualThresholdStorageKey(),JSON.stringify(accepted));renderFeatures(state.snapshot);
    $('manual-gpt-status').textContent=`${Object.keys(accepted).length}개 피처의 매수·매도 기준선을 화면에 반영했습니다. 이는 수동 참고 기준이며 자동 주문·자동 알림에는 사용되지 않습니다.`;toast('ChatGPT 수동 기준선을 반영했습니다.');
  }catch(error){$('manual-gpt-status').textContent=error.message;toast(error.message,true)}
}
function clearManualChatGptThresholds(){state.manualThresholds={};localStorage.removeItem(manualThresholdStorageKey());$('manual-gpt-response').value='';$('manual-gpt-status').textContent='이 종목의 수동 ChatGPT 기준선을 지웠습니다.';renderFeatures(state.snapshot);toast('수동 기준선을 지웠습니다.')}
async function requestAiAnalysis(type,tradeId=null){
  if(state.aiJobId)return toast('분석이 이미 진행 중입니다.',true);
  if(tradeId)activateRightTab('features');
  let rawQuestion=tradeId?autoFillPrompt(type,tradeId):$('ai-question').value.trim()||autoFillPrompt(type,tradeId);
  if(rawQuestion===state.lastAutoPrompt)rawQuestion=buildAutoPrompt(type,tradeId);
  const question=selfContainedPrompt(rawQuestion);$('ai-question').value=question;state.lastAutoPrompt=question;
  const payload={analysis_type:type,symbol:state.symbol,question,trade_id:tradeId||null};state.aiRequestedTradeId=tradeId||null;
  try{
    setAiStatus('회사·KST 기준시각·전체 피처·수급을 구조화하는 중입니다.',5,'준비');$('ai-cancel').classList.remove('hidden');$('ai-send-prompt').disabled=true;
    const job=await api('/api/ai/analyze',{method:'POST',body:JSON.stringify(payload)});state.aiJobId=job.job_id;$('ai-model').textContent=`${job.model} · 최고 추론`;
    state.aiPoll=setInterval(pollAiJob,550);await pollAiJob();
  }catch(e){finishAiJob();setAiStatus(e.message,100,'오류');toast(e.message,true)}
}
async function pollAiJob(){
  if(!state.aiJobId)return;
  try{
    const job=await api(`/api/ai/jobs/${encodeURIComponent(state.aiJobId)}`);setAiStatus(job.message,job.progress,job.status==='RUNNING'?'분석 중':job.status);
    if(job.status==='COMPLETED'){const result=job.result;finishAiJob();renderAiAnalysis(result.analysis);await syncAnalysisControls(result.analysis);setAiStatus(result.cached?'유효한 동일 데이터 분석을 재사용했습니다.':'분석 완료 · 중요 피처 상단 정렬·매수/매도 알람 준비',100,result.cached?'캐시':'완료')}
    else if(job.status==='FAILED'||job.status==='CANCELLED'){finishAiJob();setAiStatus(job.error||job.message,100,job.status==='FAILED'?'오류':'취소');if(job.status==='FAILED')toast(job.error||job.message,true)}
  }catch(e){finishAiJob();setAiStatus(e.message,100,'오류')}
}
function finishAiJob(){if(state.aiPoll)clearInterval(state.aiPoll);state.aiPoll=null;state.aiJobId=null;$('ai-cancel').classList.add('hidden');$('ai-send-prompt').disabled=false}
async function cancelAiAnalysis(){if(!state.aiJobId)return;try{await api(`/api/ai/jobs/${encodeURIComponent(state.aiJobId)}`,{method:'DELETE'});setAiStatus('취소 요청 처리 중',90,'취소 중')}catch(e){toast(e.message,true)}}
async function syncAnalysisControls(analysis){
  if(!analysis?.analysis_id||state.aiAutoAppliedId===analysis.analysis_id)return;
  state.aiAutoAppliedId=analysis.analysis_id;
  await applyAiOrder(true);
  await applyAiAlerts(true);
}
async function applyAiOrder(silent=false){
  if(!state.aiAnalysis)return toast('먼저 GPT 분석을 실행하십시오.',true);
  state.featureLayout.ai_order=[...(state.aiAnalysis.feature_order||[])];state.featureLayout.mode='ai';$('feature-sort').value='ai';await persistFeatureLayout();renderFeatures(state.snapshot);if(!silent)toast('사용자 고정 피처를 유지한 채 AI 중요 피처를 맨 위로 정렬했습니다.')
}
async function restoreFeatureOrder(){state.featureLayout.ai_order=[];state.featureLayout.mode='default';$('feature-sort').value='default';await persistFeatureLayout();renderFeatures(state.snapshot);toast('기존 피처 배열을 복원했습니다. 수동 고정은 유지됩니다.')}
async function applyAiAlerts(silent=false){
  if(!state.aiAnalysis)return toast('먼저 GPT 분석을 실행하십시오.',true);
  try{const out=await api('/api/ai/alerts/apply',{method:'POST',body:JSON.stringify({analysis_id:state.aiAnalysis.analysis_id,symbol:state.symbol})});state.snapshot.active_alerts=out.alerts;renderFeatures(state.snapshot);if('Notification'in window&&Notification.permission==='default')Notification.requestPermission();if(!silent)toast(`GPT 매수·매도 기준선 ${out.alerts.filter(a=>a.active).length}개를 실시간 알람에 등록했습니다. 실주문은 없습니다.`)}catch(e){toast(e.message,true)}
}
async function clearAiAlerts(){try{const out=await api(`/api/ai/alerts/${encodeURIComponent(state.symbol)}`,{method:'DELETE'});if(state.snapshot){state.snapshot.active_alerts=[];state.snapshot.triggered_alerts=[];renderFeatures(state.snapshot)}toast(`경고선 ${out.cleared}개를 해제했습니다.`)}catch(e){toast(e.message,true)}}
async function prepareSemiAutoResponse(){
  if(!state.aiAnalysis)return toast('먼저 GPT 분석을 실행하십시오.',true);
  const button=$('ai-prepare-response');button.disabled=true;button.textContent='현재 조건 재확인 중…';
  try{
    const playbook=await api(`/api/ai/playbook/${encodeURIComponent(state.aiAnalysis.analysis_id)}`,{method:'POST',body:'{}'});
    state.semiAutoPlaybook=playbook;
    const order=playbook.suggested_virtual_order||{},ready=playbook.status==='READY';
    if(ready&&order.reference_price_krw&&order.quantity>0){
      $('buy-price').value=Math.round(order.reference_price_krw);
      $('order-amount').value=Math.round(order.max_allocation_krw);
      $('quantity').value=order.quantity;if(['LONG','SHORT'].includes(order.side)){$('trade-side').value=order.side;$('open-buy').textContent=`${order.side} 가상 진입`}updateQuantity();
    }
    $('semi-auto-playbook').classList.remove('hidden');
    $('semi-auto-playbook').innerHTML=`<div class="playbook-head ${ready?'positive':'warning'}"><strong>${esc(playbook.status_label)}</strong><span>자동주문 없음 · 수동 확인 필수</span></div>
      <p>${esc((playbook.reasons||[]).join(' · '))}</p>
      ${(playbook.trigger_features||[]).length?`<div class="playbook-triggers">${playbook.trigger_features.map(f=>`<span><b>${esc(f.display_name||f.feature_id)}</b>${esc(f.current_display||'-')}</span>`).join('')}</div>`:''}
      <div class="playbook-order"><span>${esc(order.side||'NONE')} 참고가 ${order.reference_price_krw?fmtMoney(order.reference_price_krw):'-'}</span><span>진입 ${order.entry_price_low_krw?fmtMoney(order.entry_price_low_krw):'-'}~${order.entry_price_high_krw?fmtMoney(order.entry_price_high_krw):'-'}</span><span>손절 ${order.stop_loss_price_krw?fmtMoney(order.stop_loss_price_krw):'-'} (${order.stop_loss_pct==null?'-':signed(order.stop_loss_pct,2)+'%'})</span><span>1차 익절 ${order.take_profit_1_price_krw?fmtMoney(order.take_profit_1_price_krw):'-'}</span><span>가상 배정한도 ${fmtMoney(order.max_allocation_krw||0)}</span><span>수량 ${fmt(order.quantity||0)}주</span></div>
      <small>${esc(playbook.notice)}</small>`;
    toast(ready?'가상주문 입력값만 채웠습니다. 실행하려면 사용자가 매수 버튼을 직접 눌러야 합니다.':'아직 대응 준비 조건이 아닙니다. 입력값은 변경하지 않았습니다.',!ready);
  }catch(e){toast(e.message,true)}
  finally{button.disabled=false;button.textContent='주목 피처로 반자동 대응 준비'}
}
async function saveCurrentAi(){if(!state.aiAnalysis)return toast('저장할 분석이 없습니다.',true);try{await api(`/api/ai/analyses/${encodeURIComponent(state.aiAnalysis.analysis_id)}/save`,{method:'POST'});toast('분석 결과를 저장 표시했습니다.')}catch(e){toast(e.message,true)}}
async function showAiHistory(){
  try{const rows=await api(`/api/ai/analyses?symbol=${encodeURIComponent(state.symbol)}&limit=30`);$('ai-answer').innerHTML=rows.length?`<div class="section-title">이전 AI 분석</div>${rows.map(r=>`<button class="ai-history-row" data-ai-history="${esc(r.id)}"><strong>${esc(r.overall_action||'-')} · ${esc(r.market_regime||'-')}</strong><small>${new Date(r.requested_at).toLocaleString('ko-KR')} · ${esc(r.model)}${r.is_saved?' · 저장':''}</small></button>`).join('')}`:'<div class="ai-empty">저장된 분석이 없습니다.</div>';document.querySelectorAll('[data-ai-history]').forEach(b=>b.onclick=()=>loadAiHistory(b.dataset.aiHistory))}catch(e){toast(e.message,true)}
}
async function loadAiHistory(id){try{const out=await api(`/api/ai/analyses/${encodeURIComponent(id)}`);renderAiAnalysis(out.analysis);setAiStatus('이전 분석을 열었습니다.',100,'기록')}catch(e){toast(e.message,true)}}
function activateRightTab(name){document.querySelectorAll('.right-tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.right-content').forEach(c=>c.classList.toggle('active',c.id===`tab-${name}`))}
function effectiveBuyPrice(){return Number($('buy-price').value)||Number(state.currentTick?.price)||Number(state.bars.at(-1)?.close)||0}
function updateQuantity(){
  const price=effectiveBuyPrice();let amount=Number($('order-amount').value)||0,qty=Number($('quantity').value)||0;
  const active=document.activeElement?.id;
  if(active==='quantity'&&qty>0){amount=price*qty;$('order-amount').value=Math.floor(amount)}
  else if(price>0&&amount>0){qty=Math.floor(amount/price);$('quantity').value=Math.max(qty,0)}
  const total=price*Math.max(qty,0),costRate=(state.settings?.fee_rate||0)+(state.settings?.slippage_rate||0);
  $('estimated-cost').textContent=`예상 체결금액 ${fmt(total)}원 · 진입비용 약 ${fmt(total*costRate)}원`;
}
function toIsoOrNow(value){if(value)return new Date(value).toISOString();return new Date().toISOString()}
function featuresAtPoint(point){
  if(!point)return {...(state.snapshot?.flat||{})};
  const rows=state.rawBars.filter(r=>new Date(r.time)<=new Date(point.time));if(rows.length<5)return {...(state.snapshot?.flat||{}),price:point.price,time:point.time};
  const closes=rows.slice(-60).map(r=>Number(r.close)),volumes=rows.slice(-20).map(r=>Number(r.volume));
  const ret=n=>closes.length>n?(closes.at(-1)/closes.at(-1-n)-1)*100:0;
  const sma=n=>closes.slice(-n).reduce((a,b)=>a+b,0)/Math.min(n,closes.length);
  const avg=volumes.reduce((a,b)=>a+b,0)/Math.max(volumes.length,1),varv=volumes.reduce((a,b)=>a+(b-avg)**2,0)/Math.max(volumes.length-1,1),vz=varv?((volumes.at(-1)-avg)/Math.sqrt(varv)):0;
  return {...(state.snapshot?.flat||{}),price:point.price,time:point.time,ret_1:ret(1),ret_3:ret(3),ret_5:ret(5),ret_20:ret(20),sma20_gap:(point.price/sma(20)-1)*100,ema_gap:ret(9)-ret(21),volume_z:vz};
}
function setVirtualAutoStatus(message,tone='warning'){
  const wrap=$('ai-virtual-auto').parentElement;wrap.classList.toggle('armed',state.virtualAuto.armed);
  $('ai-virtual-auto').textContent=state.virtualAuto.armed?'자동 모의대응 ON':'자동 모의대응 OFF';
  $('ai-virtual-auto-status').textContent=message;$('ai-virtual-auto-status').className=tone;
}
function toggleVirtualAutomation(){
  if(state.virtualAuto.armed){
    state.virtualAuto.armed=false;setVirtualAutoStatus('중지됨 · 실주문 없음 · 기존 가상 포지션은 유지','unknown');return;
  }
  if(!state.aiAnalysis)return toast('먼저 GPT 조건분석을 실행하십시오.',true);
  if(new Date(state.aiAnalysis.expires_at)<=new Date())return toast('GPT 분석이 만료됐습니다. 다시 분석하십시오.',true);
  if(!confirm('GPT 기준선 충족 시 LONG/SHORT 가상거래를 자동 기록합니다. 실계좌 주문은 절대 전송하지 않습니다. 이번 실행 동안 자동 모의대응을 켭니까?'))return;
  state.virtualAuto.armed=true;state.virtualAuto.lastActionAt=0;
  setVirtualAutoStatus('감시 중 · GPT 피처 기준 + 검증 뉴스 + 가격·수급 확인 · 실주문 없음','positive');
  if('Notification'in window&&Notification.permission==='default')Notification.requestPermission();
  void monitorVirtualAutomation(state.snapshot);
}
function planTriggerMatches(trigger,flat={}){
  const value=Number(flat[trigger.feature_id]),threshold=Number(trigger.buy_threshold),second=Number(trigger.buy_threshold2);
  if(!Number.isFinite(value)||!Number.isFinite(threshold))return false;
  return trigger.buy_operator==='GT'?value>threshold:trigger.buy_operator==='GTE'?value>=threshold:trigger.buy_operator==='LT'?value<threshold:trigger.buy_operator==='LTE'?value<=threshold:trigger.buy_operator==='BETWEEN'&&Number.isFinite(second)?value>=threshold&&value<=second:false;
}
async function monitorVirtualAutomation(snapshot){
  if(!state.virtualAuto.armed||state.virtualAuto.busy||!snapshot||Date.now()-state.virtualAuto.lastActionAt<15000)return;
  const analysis=state.aiAnalysis,plan=analysis?.conditional_trade_plan||{};
  if(!analysis||new Date(analysis.expires_at)<=new Date()){
    state.virtualAuto.armed=false;setVirtualAutoStatus('분석 만료로 자동 중지 · 다시 GPT 조건분석 필요','warning');return;
  }
  const price=Number(snapshot.tick?.price||snapshot.flat?.price||state.currentTick?.price);
  if(!Number.isFinite(price)||price<=0)return setVirtualAutoStatus('유효한 현재가 대기','unknown');
  const openAuto=state.trades.find(t=>t.status==='OPEN'&&t.symbol===state.symbol&&t.mode==='virtual-auto-gpt');
  if(openAuto){
    const side=openAuto.side||'LONG',stop=Number(plan.stop_loss_price_krw),target=Number(plan.take_profit_1_price_krw);
    const stopHit=Number.isFinite(stop)&&(side==='LONG'?price<=stop:price>=stop);
    const targetHit=Number.isFinite(target)&&(side==='LONG'?price>=target:price<=target);
    const exitAlert=(snapshot.triggered_alerts||[]).some(a=>['DANGER','INVALIDATION'].includes(a.severity)&&String(a.explanation||'').includes('EXIT_REVIEW'));
    if(!stopHit&&!targetHit&&!exitAlert)return setVirtualAutoStatus(`${side} 가상 포지션 감시 · 현재 ${fmtMoney(price)} · 손절 ${fmtMoney(stop)} · 1차 ${fmtMoney(target)}`,'positive');
    state.virtualAuto.busy=true;
    try{
      const out=await api(`/api/trades/${openAuto.id}/close`,{method:'POST',body:JSON.stringify({exit_time:new Date().toISOString(),exit_price:price,exit_features:{...(snapshot.flat||{}),gpt_analysis_id:analysis.analysis_id,virtual_auto_exit_reason:stopHit?'STOP':targetHit?'TARGET':'AI_INVALIDATION'}})});
      state.virtualAuto.lastActionAt=Date.now();await refreshTrades();state.selectedTradeId=openAuto.id;renderAnalysis(out.analysis);activateRightTab('analysis');
      toast(`자동 모의 ${side} 청산: ${stopHit?'손절선':targetHit?'익절선':'GPT 무효화'} · ${signed(out.trade.net_pnl,0)}원`,Number(out.trade.net_pnl)<0);
    }catch(e){setVirtualAutoStatus(`가상 청산 실패: ${e.message}`,'negative')}
    finally{state.virtualAuto.busy=false}
    return;
  }
  if(snapshot.mode==='demo')return setVirtualAutoStatus('DEMO 가상 데이터에서는 자동 모의진입 차단 · 간편 무키 실데이터 또는 KIS 필요','warning');
  if(!plan.applicable||!['LONG','SHORT'].includes(plan.side))return setVirtualAutoStatus('GPT가 현재 진입 없음으로 판단 · 조건 대기','warning');
  if(state.trades.some(t=>t.status==='OPEN'&&t.symbol===state.symbol))return setVirtualAutoStatus('이 종목에 기존 가상 포지션이 있어 신규 진입 차단','warning');
  const triggers=plan.feature_triggers||[],matched=triggers.filter(t=>planTriggerMatches(t,snapshot.flat||{}));
  const required=Math.max(2,Math.ceil(triggers.length*.67));
  if(matched.length<required)return setVirtualAutoStatus(`피처 조건 ${matched.length}/${required} 충족 · 계속 감시`,'warning');
  const action=analysis.overall_action?.code||'',sideOk=plan.side==='LONG'?action.includes('BUY'):action.includes('SELL')||action.includes('REDUCE');
  if(!sideOk)return setVirtualAutoStatus(`GPT 종합 의견(${action})과 ${plan.side} 방향 불일치`,'warning');
  const clusters=state.newsOverview?.clusters||[];
  const newsGate=!clusters.length||(plan.side==='LONG'?(state.newsOverview?.buy_review||[]).length>0:(state.newsOverview?.attention||[]).some(x=>x.action==='SELL_REVIEW'));
  if(!newsGate)return setVirtualAutoStatus(`뉴스 검증·가격·수급 확인이 ${plan.side} 방향과 불일치`,'warning');
  const low=Number(plan.entry_price_low_krw),high=Number(plan.entry_price_high_krw);
  if(Number.isFinite(low)&&price<low||Number.isFinite(high)&&price>high)return setVirtualAutoStatus(`현재가 ${fmtMoney(price)}가 GPT 지정 범위 ${fmtMoney(low)}~${fmtMoney(high)} 밖`,'warning');
  const cash=Number(state.portfolio?.cash||0),allocation=Math.min(cash*.1,cash),qty=Math.floor(allocation/price);
  if(qty<1)return setVirtualAutoStatus('가용 현금으로 1주 가상 진입 불가','negative');
  state.virtualAuto.busy=true;
  try{
    const latestCluster=clusters[0];
    const out=await api('/api/trades/open',{method:'POST',body:JSON.stringify({symbol:state.symbol,entry_time:new Date().toISOString(),entry_price:price,quantity:qty,mode:'virtual-auto-gpt',side:plan.side,entry_features:{...(snapshot.flat||{}),gpt_analysis_id:analysis.analysis_id,gpt_virtual_auto:true,news_cluster_id:latestCluster?.cluster_id||null,matched_trigger_ids:matched.map(x=>x.feature_id)}})});
    state.virtualAuto.lastActionAt=Date.now();await refreshTrades();state.selectedTradeId=out.trade.id;
    setVirtualAutoStatus(`${plan.side} 자동 모의진입 완료 ${qty}주 · ${fmtMoney(price)} · 실주문 없음`,'positive');
    toast(`GPT 조건 충족: ${plan.side} 자동 모의진입 ${qty}주 · ${fmtMoney(price)} (실주문 없음)`);
  }catch(e){setVirtualAutoStatus(`가상 진입 실패: ${e.message}`,'negative')}
  finally{state.virtualAuto.busy=false}
}
async function saveSeedMoney(){
  const seed=Number($('seed-money').value);if(!seed||seed<=0)return;
  try{await api('/api/settings',{method:'POST',body:JSON.stringify({seed_money:seed})});state.settings.seed_money=seed;await refreshTrades();toast('시드머니를 저장했습니다.')}catch(e){toast(e.message,true)}
}
async function openVirtualTrade(){
  const price=effectiveBuyPrice(),qty=Number($('quantity').value);if(!price||!qty)return toast('매수가와 수량을 입력하십시오.',true);
  const side=$('trade-side').value||'LONG',point=state.selectedBuy||{time:new Date().toISOString(),price};
  try{
    const out=await api('/api/trades/open',{method:'POST',body:JSON.stringify({symbol:state.symbol,entry_time:toIsoOrNow(point.time),entry_price:price,quantity:qty,mode:'virtual',side,entry_features:featuresAtPoint(point)})});
    state.portfolio=out.portfolio;await refreshTrades();state.selectedTradeId=out.trade.id;toast(`${side} 가상 진입 완료: ${fmt(qty)}주 · ${fmt(price)}원`);
  }catch(e){toast(e.message,true)}
}
async function closeSelectedPosition(){
  let trade=state.trades.find(t=>t.id===state.selectedTradeId&&t.status==='OPEN');
  if(!trade)trade=state.trades.find(t=>t.status==='OPEN'&&t.symbol===state.symbol);
  if(!trade)return toast('청산할 보유 포지션을 선택하십시오.',true);
  const price=Number($('sell-price').value)||Number(state.currentTick?.price)||Number(state.bars.at(-1)?.close);const point=state.selectedSell||{time:new Date().toISOString(),price};
  try{const out=await api(`/api/trades/${trade.id}/close`,{method:'POST',body:JSON.stringify({exit_time:toIsoOrNow(point.time),exit_price:price,exit_features:featuresAtPoint(point)})});state.portfolio=out.portfolio;await refreshTrades();state.selectedTradeId=trade.id;renderAnalysis(out.analysis);activateRightTab('analysis');toast(`${trade.side||'LONG'} 가상 청산 완료: ${signed(out.trade.net_pnl,0)}원`,Number(out.trade.net_pnl)<0)}catch(e){toast(e.message,true)}
}
async function saveHistoricalTrade(){
  if(!state.selectedBuy||!state.selectedSell)return toast('차트에서 매수점과 매도점을 모두 선택하십시오.',true);
  if(new Date(state.selectedSell.time)<=new Date(state.selectedBuy.time))return toast('매도점은 매수점보다 뒤여야 합니다.',true);
  const qty=Number($('quantity').value);if(!qty)return toast('수량을 입력하십시오.',true);
  try{const out=await api('/api/trades/historical',{method:'POST',body:JSON.stringify({symbol:state.symbol,entry_time:state.selectedBuy.time,entry_price:Number($('buy-price').value)||state.selectedBuy.price,exit_time:state.selectedSell.time,exit_price:Number($('sell-price').value)||state.selectedSell.price,quantity:qty,side:$('trade-side').value||'LONG',entry_features:featuresAtPoint(state.selectedBuy),exit_features:featuresAtPoint(state.selectedSell)})});state.portfolio=out.portfolio;await refreshTrades();state.selectedTradeId=out.trade.id;renderAnalysis(out.analysis);activateRightTab('analysis');toast(`과거 ${$('trade-side').value} 거래 저장 완료: ${signed(out.trade.net_pnl,0)}원`)}catch(e){toast(e.message,true)}
}
async function deleteSelectedTrade(){if(!state.selectedTradeId)return toast('삭제할 거래를 선택하십시오.',true);if(!confirm(`거래 #${state.selectedTradeId}을 삭제합니까?`))return;try{await api(`/api/trades/${state.selectedTradeId}`,{method:'DELETE'});state.selectedTradeId=null;$('analysis-empty').classList.remove('hidden');$('analysis-result').classList.add('hidden');await refreshTrades();toast('거래를 삭제했습니다.')}catch(e){toast(e.message,true)}}
function closeWS(){if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null}}
function connectWS(){
  closeWS();const protocol=location.protocol==='https:'?'wss':'ws';const ws=new WebSocket(`${protocol}://${location.host}/ws/market/${state.symbol}`);state.ws=ws;
  ws.onopen=()=>setStatus(state.settings?.data_mode==='kis'?'KIS 실시간 연결':state.settings?.data_mode==='public'?'무키 실데이터 연결':'DEMO 시뮬레이션','live');
  ws.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.error){setStatus(m.error,'error');return}if(m.tick){updateHeader(m.tick);updateBar(m.tick)}if(m.snapshot)renderFeatures(m.snapshot)}catch(err){console.error(err)}};
  ws.onerror=()=>setStatus('실시간 연결 오류','error');
  ws.onclose=()=>{if(state.ws===ws){setStatus('재연결 중');setTimeout(()=>{if(state.ws===ws)connectWS()},1800)}};
}
async function loadSymbol(symbol,initial=false){
  setStatus('데이터 불러오는 중');state.virtualAuto.armed=false;state.virtualAuto.busy=false;state.symbol=symbol;loadManualThresholds();state.selectedBuy=null;state.selectedSell=null;state.selectedTradeId=null;state.aiAnalysis=null;state.aiAutoAppliedId=null;finishAiJob();setVirtualAutoStatus('종목 변경으로 자동 모의대응 OFF · 실주문 없음','unknown');
  try{const b=await api(`/api/bootstrap?symbol=${encodeURIComponent(symbol)}&chart_range=${encodeURIComponent(state.chartRange)}`);state.stocks=b.stocks;state.stock=b.selected;state.rawBars=b.bars;state.settings=b.settings;state.trades=b.trades;state.portfolio=b.portfolio;state.snapshot=b.snapshot;state.newsOverview=b.news||null;state.selectedNewsCluster=null;
    state.aiConfig=b.ai||null;state.featureLayout=normalizeLayout(readLocalLayout()||b.feature_layout||{});$('feature-sort').value=state.featureLayout.mode;$('ai-model').textContent=`${b.ai?.model||'gpt-5.6-sol'} · 최고 추론`;
    $('symbol-name').textContent=b.selected.name;$('symbol-code').textContent=b.selected.symbol;$('seed-money').value=b.settings.seed_money;$('order-amount').value=Math.floor((b.portfolio.cash||b.settings.seed_money)*.1);$('settings-mode').value=b.settings.data_mode;$('settings-refresh').value=b.settings.refresh_ms;$('settings-news-materiality').value=b.settings.news_min_materiality??65;$('settings-news-quality').value=b.settings.news_min_data_quality??60;$('settings-fee').value=b.settings.fee_rate;$('settings-tax').value=b.settings.tax_rate;$('settings-slippage').value=b.settings.slippage_rate;
    renderStockTabs($('stock-search').value);renderNewsOverview(b.news||{});renderFeatures(b.snapshot);renderTrades(b.trades);renderPortfolio(b.portfolio);clearSelections();
    const last=state.rawBars.at(-1);if(b.snapshot?.tick&&!b.snapshot.tick.error)updateHeader(b.snapshot.tick);else if(last)updateHeader({symbol,time:last.time,price:last.close,change:0,change_rate:0,volume:0,source:b.source,quality:b.quality});
    updateQuantity();document.querySelectorAll('#range-controls button').forEach(x=>x.classList.toggle('active',x.dataset.range===state.chartRange));$('interval-controls').classList.toggle('hidden',state.chartRange==='1mo'||state.chartRange==='3mo');if(!new URLSearchParams(location.search).has('static'))connectWS();else setStatus('정적 미리보기','live');if(!initial)toast(`${b.selected.name} · ${document.querySelector('#range-controls .active')?.textContent||state.chartRange} 차트`)
  }catch(e){setStatus(e.message,'error');toast(e.message,true)}
}
async function switchSymbol(symbol){if(symbol===state.symbol)return;await loadSymbol(symbol)}
async function changeChartRange(chartRange){
  if(chartRange===state.chartRange)return;
  state.chartRange=chartRange;state.selectedBuy=null;state.selectedSell=null;
  document.querySelectorAll('#range-controls button').forEach(x=>x.classList.toggle('active',x.dataset.range===chartRange));
  $('interval-controls').classList.toggle('hidden',chartRange==='1mo'||chartRange==='3mo');setStatus('시계열 불러오는 중');
  try{const data=await api(`/api/chart/${encodeURIComponent(state.symbol)}?chart_range=${encodeURIComponent(chartRange)}`);state.rawBars=data.bars;$('data-source').textContent=data.source;renderChart();clearSelections();setStatus(state.settings?.data_mode==='kis'?'KIS 실시간 연결':state.settings?.data_mode==='public'?'무키 실데이터 연결':'DEMO 시뮬레이션','live')}catch(e){setStatus(e.message,'error');toast(e.message,true)}
}
function openSettings(){$('settings-modal').classList.remove('hidden')}
function closeSettings(){$('settings-modal').classList.add('hidden')}
function openApiSetup(){$('api-setup-modal').classList.remove('hidden')}
function hideApiSetup(){$('api-setup-modal').classList.add('hidden')}
function clearApiInputs(){['setup-kis-key','setup-kis-secret','setup-openai-key','setup-dart-key','setup-ecos-key','setup-naver-id','setup-naver-secret'].forEach(id=>$(id).value='')}
function apiSetupPayload(){const kisAppKey=$('setup-kis-key').value.trim(),kisAppSecret=$('setup-kis-secret').value.trim(),hasNewKisCredentials=Boolean(kisAppKey&&kisAppSecret);return {
  kis_mode:$('setup-kis-mode').value,kis_app_key:kisAppKey||null,
  kis_app_secret:kisAppSecret||null,openai_api_key:$('setup-openai-key').value.trim()||null,
  open_dart_api_key:$('setup-dart-key').value.trim()||null,ecos_api_key:$('setup-ecos-key').value.trim()||null,
  naver_client_id:$('setup-naver-id').value.trim()||null,naver_client_secret:$('setup-naver-secret').value.trim()||null,
  // Saving a newly entered KIS key must also apply it.  The checkbox remains
  // useful when the user wants to re-enable already stored credentials.
  activate_kis:hasNewKisCredentials||$('setup-activate-kis').checked
}}
function setupStatusText(provider,labels){
  if(provider?.connected)return labels.connected;
  if(provider?.configured)return labels.configured;
  return labels.missing;
}
function renderSetupStatus(status){
  state.setup=status;const p=status.providers||{};
  $('setup-kis-mode').value=status.kis_mode||'real';
  $('setup-public-status').textContent=p.public_market?.connected?'사용 중':'바로 사용 가능';
  $('setup-kis-status').textContent=setupStatusText(p.kis,{connected:'연결 검증됨',configured:'저장됨/검증 필요',missing:'API 필요'});
  $('setup-openai-status').textContent=setupStatusText(p.openai,{connected:'연결 검증됨',configured:'저장됨/검증 필요',missing:'API 필요'});
  $('setup-dart-status').textContent=p.open_dart?.connected?'키 보강 연결됨':p.open_dart?.configured?'저장됨/검증 필요':'무키 RSS만';
  $('setup-naver-status').textContent=p.naver_news?.connected?'검색 API 연결됨':p.naver_news?.configured?'저장됨/검증 필요':'무키 금융뉴스만';
  $('setup-ecos-status').textContent=p.ecos?.connected?'공식 통계 연결됨':p.ecos?.configured?'저장됨/검증 필요':'무키 sample만';
  for(const [id,key] of [['setup-kis-detail','kis'],['setup-openai-detail','openai'],['setup-dart-detail','open_dart'],['setup-naver-detail','naver_news'],['setup-ecos-detail','ecos']]){
    const row=p[key]||{},features=(row.fills_features||[]).join(' · '),time=row.last_success_at?` · 마지막 성공 ${new Date(row.last_success_at).toLocaleString('ko-KR')}`:'';
    $(id).textContent=`${row.purpose||'보강 기능'}${features?` · 채우는 기능: ${features}`:''}${row.message?` · 상태: ${row.message}`:''}${time}`;
  }
  $('setup-truth').className='setup-truth live';
  $('setup-truth').textContent=p.kis?.connected?'KIS 정밀 보강 모드입니다. 실패 시 무키 공개 시세로 안전하게 대체합니다.':'간편 무키 실데이터가 연결됩니다. KIS·OpenDART·ECOS·네이버 개발자 키는 필수가 아닙니다.';
}
function renderValidation(results){
  const names={openai:'OpenAI',open_dart:'OpenDART',ecos:'ECOS',naver_news:'네이버 검색',kis:'KIS'},box=$('setup-validation');
  box.classList.remove('hidden');box.innerHTML=Object.entries(results||{}).map(([key,row])=>{const cls=row.ok?'ok':row.state==='NOT_CONFIGURED'?'missing':'bad';return `<div class="validation-row"><strong>${esc(names[key]||key)}</strong><span class="${cls}">${esc(row.state||'-')}</span><span>${esc(row.message||'')}</span></div>`}).join('');
}
async function loadSetupStatus(){
  try{const status=await api('/api/setup/status');renderSetupStatus(status);if(status.needs_setup)openApiSetup();return status}
  catch(e){console.warn('API 키 상태 확인 실패',e.message);return null}
}
async function saveApiSetup(){
  const saveBtn=$('api-setup-save'),payload=apiSetupPayload();
  saveBtn.disabled=true;saveBtn.textContent='암호화 저장 · 실제 연결 확인 중…';$('setup-message').textContent='Windows 사용자 전용 암호화 저장 후 입력된 공급자를 실제 호출합니다.';
  try{
    const out=await api('/api/setup/credentials',{method:'POST',headers:{'X-Setup-Intent':'save-local-api-keys'},body:JSON.stringify(payload)});
    clearApiInputs();renderSetupStatus(out);renderValidation(out.validation);$('setup-message').textContent='입력한 API를 암호화 저장했습니다. 아래에서 공급자별 실제 연결 결과를 확인하십시오.';
    $('setup-message').className='setup-message success';
    await loadSymbol(state.symbol,true);
  }catch(e){clearApiInputs();$('setup-message').textContent=e.message;$('setup-message').className='setup-message error'}
  finally{Object.keys(payload).forEach(k=>payload[k]=null);saveBtn.disabled=false;saveBtn.textContent='입력한 API 저장 후 시작'}
}
async function testApiSetup(){const button=$('api-setup-test'),payload=apiSetupPayload();button.disabled=true;button.textContent='실제 연결 확인 중…';try{const out=await api('/api/setup/test',{method:'POST',body:JSON.stringify(payload)});renderValidation(out.results);$('setup-message').textContent='저장하지 않고 실제 연결만 시험했습니다.';$('setup-message').className='setup-message success'}catch(e){$('setup-message').textContent=e.message;$('setup-message').className='setup-message error'}finally{Object.keys(payload).forEach(k=>payload[k]=null);button.disabled=false;button.textContent='연결 테스트'}}
async function completeApiSetup(choice){try{const out=await api('/api/setup/complete',{method:'POST',body:JSON.stringify({choice})});renderSetupStatus(out);hideApiSetup();return out}catch(e){$('setup-message').textContent=e.message;$('setup-message').className='setup-message error';throw e}}
async function closeApiSetup(){if(state.setup?.needs_setup){await completeApiSetup('later')}else hideApiSetup()}
async function startPublicMode(){
  const button=$('api-setup-skip');button.disabled=true;button.textContent='무키 실데이터 연결 중…';
  try{await completeApiSetup('public');if(state.settings)state.settings.data_mode='public';await loadSymbol(state.symbol,true);toast('공개 실데이터 모드로 시작했습니다. 키 필요 피처는 화면에 API 이름과 함께 표시됩니다.')}
  catch(e){$('setup-message').textContent=e.message;$('setup-message').className='setup-message error'}
  finally{button.disabled=false;button.textContent='API 없이 공개 데이터로 시작'}
}
async function saveSettings(){
  const payload={data_mode:$('settings-mode').value,refresh_ms:Number($('settings-refresh').value),news_min_materiality:Number($('settings-news-materiality').value),news_min_data_quality:Number($('settings-news-quality').value),fee_rate:Number($('settings-fee').value),tax_rate:Number($('settings-tax').value),slippage_rate:Number($('settings-slippage').value),seed_money:Number($('seed-money').value)};
  try{const out=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});closeSettings();toast(out.restart_required?'설정 저장 완료. 데이터 모드 변경은 서버 재시작 후 적용됩니다.':'설정을 저장했습니다.');state.settings={...state.settings,...payload};await refreshTrades()}catch(e){toast(e.message,true)}
}
function bindUI(){
  document.querySelectorAll('.rail-btn').forEach(b=>b.onclick=()=>{const tool=b.dataset.tool;if(tool==='buy'||tool==='sell'){state.selectionMode=tool;document.querySelectorAll('.select-mode').forEach(x=>x.classList.toggle('active',x.dataset.mode===tool))}else if(tool==='cursor'){state.selectionMode='inspect';document.querySelectorAll('.select-mode').forEach(x=>x.classList.toggle('active',x.dataset.mode==='inspect'))}else if(tool==='reset'){Plotly.relayout('main-chart',{'xaxis.autorange':true,'yaxis.autorange':true});clearSelections()}else if(tool==='fullscreen'){document.documentElement.requestFullscreen?.()}document.querySelectorAll('.rail-btn').forEach(x=>x.classList.toggle('active',x===b))});
  document.querySelectorAll('.select-mode').forEach(b=>b.onclick=()=>{state.selectionMode=b.dataset.mode;document.querySelectorAll('.select-mode').forEach(x=>x.classList.toggle('active',x===b))});
  document.querySelectorAll('#interval-controls button').forEach(b=>b.onclick=()=>{state.interval=b.dataset.interval;document.querySelectorAll('#interval-controls button').forEach(x=>x.classList.toggle('active',x===b));renderChart()});
  document.querySelectorAll('#range-controls button').forEach(b=>b.onclick=()=>changeChartRange(b.dataset.range));
  $('candle-mode').onclick=()=>{state.chartMode='candle';$('candle-mode').classList.add('active');$('line-mode').classList.remove('active');renderChart(true)};$('line-mode').onclick=()=>{state.chartMode='line';$('line-mode').classList.add('active');$('candle-mode').classList.remove('active');renderChart(true)};
  $('refresh-btn').onclick=()=>loadSymbol(state.symbol);$('clear-selection').onclick=clearSelections;
  document.querySelectorAll('.right-tab').forEach(b=>b.onclick=()=>activateRightTab(b.dataset.tab));
  $('feature-search').oninput=()=>renderFeatures(state.snapshot);$('feature-group-filter').onchange=()=>renderFeatures(state.snapshot);$('feature-sort').onchange=async e=>{state.featureLayout.mode=e.target.value;await persistFeatureLayout();renderFeatures(state.snapshot)};
  $('stock-search').oninput=e=>renderStockTabs(e.target.value);
  $('buy-price').oninput=updateQuantity;$('order-amount').oninput=updateQuantity;$('quantity').oninput=updateQuantity;$('trade-side').onchange=e=>$('open-buy').textContent=`${e.target.value} 가상 진입`;
  document.querySelectorAll('[data-ratio]').forEach(b=>b.onclick=()=>{const cash=Number(state.portfolio?.cash||state.settings?.seed_money||0);$('order-amount').value=Math.floor(cash*Number(b.dataset.ratio));updateQuantity()});
  $('seed-money').onchange=saveSeedMoney;$('open-buy').onclick=openVirtualTrade;$('close-sell').onclick=closeSelectedPosition;$('save-history').onclick=saveHistoricalTrade;$('delete-trade').onclick=deleteSelectedTrade;
  $('settings-btn').onclick=openSettings;$('settings-close').onclick=closeSettings;$('settings-save').onclick=saveSettings;$('settings-modal').onclick=e=>{if(e.target===$('settings-modal'))closeSettings()};
  $('api-key-btn').onclick=openApiSetup;$('api-setup-close').onclick=closeApiSetup;$('api-setup-skip').onclick=startPublicMode;$('api-setup-save').onclick=saveApiSetup;$('api-setup-test').onclick=testApiSetup;$('api-setup-clear').onclick=()=>{clearApiInputs();$('setup-message').textContent='현재 입력칸을 지웠습니다. 이미 암호화 저장된 키는 유지됩니다.'};$('api-setup-later').onclick=()=>completeApiSetup('later');$('api-setup-reconfigure').onclick=()=>{clearApiInputs();document.querySelector('.setup-optional').open=true;$('setup-openai-key').focus();$('setup-message').textContent='바꿀 키만 입력하십시오. 빈칸은 기존 암호화 저장값을 유지합니다.'};$('api-setup-modal').onclick=e=>{if(e.target===$('api-setup-modal'))closeApiSetup()};
  $('news-refresh-btn').onclick=refreshNews;
  document.querySelectorAll('[data-ai-type]').forEach(b=>b.onclick=()=>requestAiAnalysis(b.dataset.aiType));$('ai-auto-prompt').onclick=()=>{autoFillPrompt('SYMBOL');toast('트리거 피처 이름·값·의미·출처를 포함한 질문을 자동 생성했습니다.')};$('ai-send-prompt').onclick=()=>requestAiAnalysis('SYMBOL');$('ai-cancel').onclick=cancelAiAnalysis;$('ai-apply-order').onclick=()=>applyAiOrder(false);$('ai-restore-order').onclick=restoreFeatureOrder;$('ai-apply-alerts').onclick=()=>applyAiAlerts(false);$('ai-clear-alerts').onclick=clearAiAlerts;$('ai-prepare-response').onclick=prepareSemiAutoResponse;$('ai-virtual-auto').onclick=toggleVirtualAutomation;$('ai-save').onclick=saveCurrentAi;$('ai-history').onclick=showAiHistory;
  $('manual-gpt-open').onclick=openManualChatGpt;$('manual-gpt-prompt').onclick=()=>{const prompt=buildManualChatGptPrompt();downloadTextFile(`${state.symbol}-chatgpt-manual-analysis.md`,prompt,'text/markdown;charset=utf-8');copyText(prompt).then(copied=>{$('manual-gpt-status').textContent=copied?'프롬프트 파일을 저장하고 클립보드에도 복사했습니다.':'프롬프트 파일을 저장했습니다.'})};$('manual-gpt-apply').onclick=applyManualChatGptResponse;$('manual-gpt-clear').onclick=clearManualChatGptThresholds;
  window.addEventListener('resize',()=>Plotly.Plots.resize('main-chart'));
}
async function init(){startClock();bindUI();await loadSetupStatus();await loadSymbol(state.symbol,true)}
window.addEventListener('load',init);
