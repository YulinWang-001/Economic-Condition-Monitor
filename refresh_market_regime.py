"""Refresh the US Market Regime Dashboard with Bloomberg and validate in headless Edge.
Run python refresh_market_regime.py [--reuse]. Other dashboards are not changed.
"""
import ast,copy,datetime as dt,hashlib,html,json,math,re,shutil,subprocess,sys,time
from pathlib import Path
from zoneinfo import ZoneInfo
import blpapi
import numpy as np
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P))
TARGET=P/'Market_Regime_Dashboard.html'
STAGED=P/'Market_Regime_Dashboard.pending.html'
CACHE=P/'market_regime_bloomberg_latest.json'
now=dt.datetime.now(dt.timezone.utc)
london=now.astimezone(ZoneInfo('Europe/London'))
end=london.date()
backup=P/'backups'/('market_regime_refresh_'+london.strftime('%Y%m%d_%H%M%S'))
backup.mkdir(parents=True,exist_ok=True)
shutil.copy2(TARGET,backup/TARGET.name)
for filename in ['market_regime_bloomberg_latest.json','market_regime_refresh_qa.json']:
 if (P/filename).exists():shutil.copy2(P/filename,backup/filename)
template=TARGET.read_text(encoding='utf-8')
old=json.loads(re.search(r'^const ALL = (.*);$',template,re.M)[1])
oldpe=json.loads(re.search(r'^const PE_DATA = (.*);$',template,re.M)[1])
M=json.loads((P/'bloomberg_dashboard_map.json').read_text(encoding='utf-8'))
equities=sorted({v for v in M['US'].values() if v.endswith(' Equity')})
other=sorted((set(M['US'].values())|set(M['MAC'].values())|{'SKEW Index','CCMP Index','NDX Index','MOVE Index'})-set(equities))
pe_tickers=sorted({v[0] for v in M['PEUS'].values()})
opts=[M['US'][s] for s in ['SPY','QQQ','SMH','NVDA']]
groups=[
 (equities,['PX_LAST','PX_VOLUME','TURNOVER'],'19990310',None),
 (other,['PX_LAST'],'19900101',None),
 (pe_tickers,['BEST_PE_RATIO'],'20050101',{'BEST_FPERIOD_OVERRIDE':'1BF'}),
 (opts,['PUT_CALL_OPEN_INTEREST_RATIO','30DAY_IMPVOL_100.0%MNY_DF'],'20200101',None)]
if '--reuse' in sys.argv:
 R=json.loads(CACHE.read_text(encoding='utf-8'))
 assert R['requested_end']==str(end)
 print('Reusing Bloomberg snapshot',R['fetched'],flush=True)
else:
 R={'fetched':now.isoformat(),'requested_end':str(end),'source':'Bloomberg Desktop API','history':{},'errors':[],'requests':[]}
 opt=blpapi.SessionOptions();opt.setServerHost('localhost');opt.setServerPort(8194)
 session=blpapi.Session(opt)
 if not session.start():raise RuntimeError('Bloomberg Desktop API connection failed')
 try:
  assert session.openService('//blp/refdata')
  svc=session.getService('//blp/refdata')
  for securities,fields,start,overrides in groups:
   for off in range(0,len(securities),10):
    batch=securities[off:off+10]
    req=svc.createRequest('HistoricalDataRequest')
    for security in batch:req.getElement('securities').appendValue(security)
    for field in fields:req.getElement('fields').appendValue(field)
    req.set('startDate',start);req.set('endDate',end.strftime('%Y%m%d'));req.set('periodicitySelection','DAILY')
    for adj in ['adjustmentSplit','adjustmentNormal','adjustmentAbnormal']:req.set(adj,True)
    for field,setting in (overrides or {}).items():
     ov=req.getElement('overrides').appendElement();ov.setElement('fieldId',field);ov.setElement('value',setting)
    R['requests'].append({'securities':batch,'fields':fields,'start':start,'end':str(end),'overrides':overrides,'adjustments':'split and cash distributions'})
    session.sendRequest(req)
    deadline=time.monotonic()+240;heartbeat=time.monotonic()
    while True:
     ev=session.nextEvent(5000)
     for msg in ev:
      if msg.hasElement('responseError'):raise RuntimeError(str(msg))
      if not msg.hasElement('securityData'):continue
      sd=msg.getElement('securityData');security=sd.getElementAsString('security')
      if sd.hasElement('securityError'):
       R['errors'].append({'security':security,'error':str(sd.getElement('securityError'))});continue
      for exception in sd.getElement('fieldExceptions').values():
       R['errors'].append({'security':security,'error':str(exception)})
      dest=R['history'].setdefault(security,{})
      for row in sd.getElement('fieldData').values():
       day=str(row.getElementAsDatetime('date'))[:10];record=dest.setdefault(day,{})
       for field in fields:
        if row.hasElement(field) and not row.getElement(field).isNull():
         v=row.getElementAsFloat(field)
         if math.isfinite(v):record[field]=v
     if ev.eventType()==blpapi.Event.RESPONSE:break
     if time.monotonic()>deadline:raise TimeoutError('Bloomberg request timeout')
     if time.monotonic()-heartbeat>25:
      print('Bloomberg request still running:',batch[0],fields,flush=True);heartbeat=time.monotonic()
    print('Fetched',len(batch),'securities:',','.join(fields),flush=True)
 finally:session.stop()
 cachetmp=CACHE.with_suffix('.json.tmp')
 cachetmp.write_text(json.dumps(R,separators=(',',':'),allow_nan=False),encoding='utf-8')
 cachetmp.replace(CACHE)
FETCH=R['fetched']
REPORT={'fetched':FETCH,'requested_end':str(end),'backup':str(backup),'series':{},'warnings':[],'api_exceptions':R['errors']}
import market_regime_html as us
import market_regime_macro as macro_tools
# Reuse audited transformations without executing the legacy builder's two-dashboard side effects.
fnames={'series','frame','array','dates','prices','blob','replace_data','enddate','pe_data','option_data'}
tree=ast.parse((P/'build_bloomberg_dashboards.py').read_text(encoding='utf-8'))
helpers=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in fnames],type_ignores=[])
exec(compile(helpers,'bloomberg_dashboard_helpers','exec'),globals())
assert fnames.issubset(globals())
upx=frame(M['US']);macro=frame(M['MAC'])
us.mrd.load_yahoo=lambda:upx.rename(columns=us.mrd.YF_TICKERS).dropna(subset=['SPY']).loc[us.mrd.START:]
us.mrd.load_fred=lambda:macro
def download(tickers,**kwargs):
 df=upx[list(tickers)].loc[kwargs.get('start','1999-01-01'):]
 return pd.concat({'Close':df},axis=1)
us.od.download=download
us.od.SOURCE.update({t:'Bloomberg '+security for t,security in M['US'].items()})
def numerator_volume(pairs):
 out={}
 for pair in pairs:
  leg=pair.get('ta',pair['a'])
  if isinstance(leg,list):
   members=[series(M['US'][ticker],'TURNOVER',False) for ticker in leg]
   if all(len(member) for member in members):
    out[pair['key']]=(pd.concat(members,axis=1).sum(axis=1,min_count=len(leg)),'usd')
  elif M['US'].get(leg,'').endswith(' Equity'):
   volume=series(M['US'][leg],'PX_VOLUME',False)
   if len(volume):out[pair['key']]=(volume,'shares')
 return out
us.numerator_volume=numerator_volume
ud=copy.deepcopy(old)
ud.update(regime=us.build_regime(),pairs=us.build_pairs(),mom=us.build_momentum(),source='Bloomberg Desktop API',fetched=FETCH)
ud['asof']=ud['regime']['asof']
ny=now.astimezone(ZoneInfo('America/New_York'))
ud['final']=ud['asof']<ny.date().isoformat() or (ny.hour,ny.minute)>=(16,20)
ud['volume_source']='Bloomberg PX_VOLUME for shares; sum of TURNOVER for basket dollar turnover; no approximations or forward-filled volumes.'
for pair in ud['pairs']:
 pair['note']=re.sub(r'Yahoo|OpenD|moomoo','Bloomberg',pair.get('note',''))
 if pair.get('v'):pair['volume_source']='Bloomberg TURNOVER (basket sum, USD)' if pair.get('vk')=='usd' else 'Bloomberg PX_VOLUME (shares)'
sp=copy.deepcopy(old['sectorPerformance'])
sp.update(source='Bloomberg Desktop API',downloaded=FETCH,basis='Split- and cash-distribution-adjusted Bloomberg PX_LAST',asof=ud['asof'])
sp.pop('refresh_warning',None)
sp['prices']={t:prices(upx[t]) for t in ['SPY']+[v['ticker'] for v in sp['sectors']]}
sp['sourceNote']='Bloomberg adjusted ETF prices; actual observation dates retained.'
ud['sectorPerformance']=sp
ud['options']=option_data(old['options'],{t:M['US'][t] for t in ['SPY','QQQ','SMH','NVDA']})
upe=pe_data(M['PEUS']);upe['field']='BEST_PE_RATIO; BEST_FPERIOD_OVERRIDE=1BF (blended forward 12 months)'
ud['macro']=macro_tools.build_data(R['history'],R.get('macro_fetched',FETCH))
page=replace_data(template,'ALL',ud);page=replace_data(page,'PE_DATA',upe)
page=macro_tools.install(page)
page=page.replace('share volume"} from OpenD','share volume"} from Bloomberg').replace('No OpenD volume for','No Bloomberg volume for')
page=page.replace("numerator's daily volume (OpenD)","numerator's daily volume (Bloomberg)")
page=re.sub(r'<li>Sources: Bloomberg Desktop API, refreshed .*?</li>',
 '<li>Sources: Bloomberg Desktop API, refreshed '+FETCH+'. Adjusted ETF closes; generic front-month copper/gold; VIX/VIX3M; LF98OAS credit spread; USGGT10Y real yield; USTWBGD broad dollar; FARBAST minus FARBDTRS (USD millions) minus RRPQTOON (USD billions) for net liquidity. Macro series retain actual publication dates. Volume is PX_VOLUME; basket turnover is summed TURNOVER in USD. Options and cached chain analytics retain their own dates.</li>',page,count=1,flags=re.S)
REPORT.update(asof=ud['asof'],final=ud['final'],score=ud['regime']['score'][-1],pairs=len(ud['pairs']),pe_series=len(upe['series']))
rg=ud['regime'];n=len(rg['d'])
assert n>252 and rg['d']==sorted(set(rg['d']))
assert len(rg['spy'])==len(rg['score'])==n and all(len(v)==n for v in rg['z'].values())
assert math.isfinite(rg['score'][-1])
assert ud['asof']>=old['asof'],(ud['asof'],old['asof'])
assert (end-dt.date.fromisoformat(ud['asof'])).days<=4,('Stale benchmark',ud['asof'])
assert len(ud['pairs'])==len(old['pairs']) and len(upe['series'])==len(oldpe['series'])
for pair in ud['pairs']:
 n=len(pair['d'])
 assert n==len(pair['q'])==len(pair['s'])>0 and pair['d']==sorted(set(pair['d']))
 assert all(v>0 for v in pair['q']+pair['s'])
 prior=next(p for p in old['pairs'] if p['key']==pair['key'])
 if prior.get('v'):assert pair.get('v') and len(pair['v'])==n,('Lost volume',pair['key'])
 if pair.get('v'):
  assert all(v is None or v>=0 for v in pair['v'])
  assert any(v is not None for v in pair['v'][-5:]),('Stale volume',pair['key'])
for item in upe['series']:assert len(item['v'])==len(upe['d'])
for ticker,item in ud['options']['by'].items():
 assert item['price']['d'][-1]>=old['options']['by'][ticker]['price']['d'][-1]
 for row in item['oi_rows']:assert row.get('put_oi') is None and row.get('call_oi') is None
REPORT['options_last_dates']={t:{'price':v['price']['d'][-1],'iv':v['vol_rows'][-1]['d'] if v['vol_rows'] else None,'oi':v['oi_rows'][-1]['d'] if v['oi_rows'] else None} for t,v in ud['options']['by'].items()}
REPORT['macro_last_dates']={name:enddate(series(security)) for name,security in M['MAC'].items()}
REPORT['source_last_dates']={k:v['last'] for k,v in REPORT['series'].items()}
# Independently reconcile the final score using the established 252/63/5-session rules.
px=us.mrd.load_yahoo().ffill().dropna(subset=['SPY'])
ind,_=us.mrd.build_indicators(px,macro)
z=(ind-ind.rolling(252).mean())/ind.rolling(252).std()
score=z.clip(-3,3).mean(axis=1).rolling(5).mean()
assert math.isclose(round(score.loc[pd.Timestamp(ud['asof'])],3),rg['score'][-1],abs_tol=1e-12)
# Cached chain/delta-skew analytics retain their true source observations.
if 'skew' in old['options']:
 for key,v in old['options']['skew'].items():
  if key not in ['skew','refresh_warning']:assert ud['options']['skew'][key]==v,('Legacy analytics altered',key)
STAGED.write_text(page,encoding='utf-8')
print('Staged',STAGED.name,'asof',ud['asof'],'final',ud['final'],'score',REPORT['score'],flush=True)
# Run the actual HTML in Edge without installing another browser package.
edge=Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
assert edge.exists()
harness=r"""
<script>
(async()=>{
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 const check=(x,m)=>{if(!x)throw Error(m)};
 const result={tabs:[],ratioViews:0,optionsViews:0,peControls:0,errors:window.__refreshErrors};
 try{
  await wait(200);
  const keys=[...document.querySelectorAll('#nav button')].map(b=>b.dataset.pg);
  check(JSON.stringify(keys)===JSON.stringify(['regime','macro','vol','ratios','sectorperformance','options','momentum','pe']),'Missing tabs');
  for(const key of keys){
   document.querySelector('#nav button[data-pg="'+key+'"]').click();await wait(60);
   const panel=document.getElementById('pg-'+key);
   check(getComputedStyle(panel).display!=='none','Hidden panel '+key);
   check(panel.querySelectorAll('svg').length>0,'No charts '+key);
   check(!/NaN|Infinity/.test(panel.innerHTML),'Invalid numeric output '+key);
   check(!panel.innerText.includes('$'+'{ALL.'),'Unresolved template '+key);
   if(key==='ratios'){
    for(const group of [...document.querySelectorAll('#grpSeg button')]){
     group.click();await wait(20);
     for(const button of [...document.querySelectorAll('#tabs button')]){
      button.click();await wait(10);
      check(document.querySelector('#c1 svg'),'Missing ratio chart');
      check(!/NaN|Infinity/.test(panel.innerHTML),'Invalid ratio output');result.ratioViews++;
     }
    }
   }
   if(key==='options'){
    for(const button of [...document.querySelectorAll('#opSub button')]){
     button.click();await wait(20);
     for(const period of ['1M','MAX']){
      const button=document.querySelector('#opPeriods button[data-op-period="'+period+'"]');
      if(button)button.click();await wait(20);
      check(!/NaN|Infinity/.test(panel.innerHTML),'Invalid options output');result.optionsViews++;
     }
    }
   }
   if(key==='pe'){
    for(const period of ['3Y','5Y','10Y','Max']){
     const button=document.querySelector('#pePer button[data-p="'+period+'"]');check(button,'Missing PE period');button.click();await wait(20);result.peControls++;
    }
    for(const mode of ['abs','rel']){
     const button=document.querySelector('#peMode button[data-m="'+mode+'"]');check(button,'Missing PE mode');button.click();await wait(20);result.peControls++;
    }
    check(!/NaN|Infinity/.test(panel.innerHTML),'Invalid PE output');
   }

   if(key==='macro'){
    result.macroControls=0;
    for(const index of ['composite','nasdaq100']){
     document.querySelector('#macroIndex button[data-index="'+index+'"]').click();
     for(const period of ['1M','3M','6M','1Y','3Y','5Y','10Y','Max']){
      document.querySelector('#macroPer button[data-period="'+period+'"]').click();
      for(const mode of ['levels','indexed']){
       document.querySelector('#macroMode button[data-mode="'+mode+'"]').click();await wait(10);
       const plot=document.getElementById('macroChart');
       check(plot.dataset.index===index&&plot.dataset.period===period&&plot.dataset.mode===mode,'Macro control mismatch');
       check(plot.querySelectorAll('svg > path').length>=(mode==='levels'?3:2),'Missing Nasdaq/MOVE series');
       check(!/NaN|Infinity/.test(plot.innerHTML),'Invalid Macro numeric output');
       check(document.getElementById('macroSource').textContent.includes('same-date observations'),'Missing Macro source note');
       result.macroControls++;
      }
     }
    }

    result.relationshipControls=0;
    for(const index of ['composite','nasdaq100']){
     document.querySelector('#macroIndex button[data-index="'+index+'"]').click();
     for(const window of ['20','60','120']){
      document.querySelector('#macroLookback button[data-lookback="'+window+'"]').click();await wait(20);
      const correlation=document.getElementById('macroCorrelation'),direction=document.getElementById('macroDirection');
      check(correlation.querySelector('svg')&&direction.querySelector('svg'),'Missing relationship charts');
      check(correlation.dataset.lookback===window&&direction.dataset.index===index,'Relationship selector mismatch');
      const data=MACRO.indices[index],last=data.d.length-1,w=data.relationship.windows[window];
      check(Number.isFinite(w.correlation[last])&&w.correlation[last]>=-1&&w.correlation[last]<=1,'Invalid correlation range');
      check(Number.isFinite(w.same_direction_share[last])&&w.same_direction_share[last]>=0&&w.same_direction_share[last]<=1,'Invalid direction share');
      check(Math.abs(Number(correlation.dataset.latest)-w.correlation[last])<1e-12,'Wrong correlation displayed');
      check(Math.abs(Number(direction.dataset.latest)-w.same_direction_share[last])<1e-12,'Wrong direction share displayed');
      check(!/NaN|Infinity/.test(document.getElementById('pg-macro').innerHTML),'Invalid relationship output');
      result.relationshipControls++;
     }
    }
    document.querySelector('#macroLookback button[data-lookback="60"]').click();

    document.querySelector('#macroIndex button[data-index="composite"]').click();
    document.querySelector('#macroPer button[data-period="3Y"]').click();
    document.querySelector('#macroMode button[data-mode="levels"]').click();
    const hit=document.querySelector('#macroChart .hit'),box=hit.getBoundingClientRect();
    hit.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:box.x+box.width/2,clientY:box.y+box.height/2}));
    check(document.getElementById('tip').textContent.includes('MOVE'),'MOVE hover missing');
    check(document.getElementById('tip').textContent.includes('Nasdaq'),'Nasdaq hover missing');
    hit.dispatchEvent(new MouseEvent('mouseleave',{bubbles:true}));
   }
   if(key==='momentum'){
    for(const id of ['moEtfSeg','moBmSeg','moPerSeg'])
     for(const button of [...document.querySelectorAll('#'+id+' button')]){button.click();await wait(10);}
    check(!/NaN|Infinity/.test(panel.innerHTML),'Invalid momentum output');
   }
   result.tabs.push({key,svg:panel.querySelectorAll('svg').length});
  }
  showPage("macro");
  document.getElementById("macroRelationshipPanel").scrollIntoView({block:"start"});
  check(result.errors.length===0,'JavaScript errors: '+JSON.stringify(result.errors));
  result.status='pass';result.viewport={width:innerWidth,height:innerHeight};
 }catch(e){result.status='fail';result.failure=String(e);}
 const output=document.createElement('pre');output.id='refreshQA';output.textContent=JSON.stringify(result);document.body.appendChild(output);
})();
</script>
"""
testpage=backup/'market_regime_browser_test.html'
tested=page.replace('<script>',"<script>window.__refreshErrors=[];window.addEventListener('error',e=>window.__refreshErrors.push(e.message));</script><script>",1)
tested=tested.replace('</body>',harness+'</body>')
testpage.write_text(tested,encoding='utf-8')
REPORT['macro_tab']={key:{'first':record['d'][0],'last':record['d'][-1],'observations':len(record['d']),'nasdaq_source_last':record['nasdaq_last_date'],'move_source_last':record['move_last_date']} for key,record in ud['macro']['indices'].items()}
browser_reports=[]
for mobile in [False,True]:
 profile=backup/('edge_mobile_profile' if mobile else 'edge_desktop_profile')
 assert profile.resolve().is_relative_to(backup.resolve())
 startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
 args=[str(edge),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--allow-file-access-from-files','--user-data-dir='+str(profile),'--virtual-time-budget=20000','--window-size='+('390,844' if mobile else '1440,1000'),'--dump-dom']
 args.append('--screenshot='+str(backup/('Macro_mobile.png' if mobile else 'Macro_desktop.png')))
 if mobile:args.append('--force-dark-mode')
 args.append(testpage.as_uri()+'#regime')
 output=subprocess.run(args,capture_output=True,timeout=70,startupinfo=startup,creationflags=subprocess.CREATE_NO_WINDOW)
 dom=output.stdout.decode('utf-8',errors='replace')
 marker=re.search(r'<pre id="refreshQA">(.*?)</pre>',dom,re.S)
 if not marker:
  (backup/('edge_'+('mobile' if mobile else 'desktop')+'_stderr.txt')).write_bytes(output.stderr)
  raise RuntimeError('Browser QA result missing; original dashboard remains unchanged')
 result=json.loads(html.unescape(marker[1]));result['mode']='mobile-dark' if mobile else 'desktop'
 browser_reports.append(result)
 assert result['status']=='pass',result
 print('Browser PASS',result['mode'],len(result['tabs']),'tabs;',result['ratioViews'],'ratio views;',result['optionsViews'],'option views',flush=True)
REPORT['macro_relationship']={key:{'latest':data['d'][-1],'history':data['relationship']['history'],'windows':{size:{'correlation':v['correlation'][-1],'same_direction_share':v['same_direction_share'][-1],'directional_count':v['directional_count'][-1]} for size,v in data['relationship']['windows'].items()}} for key,data in ud['macro']['indices'].items()}
REPORT['browser']=browser_reports
REPORT['sha256']=hashlib.sha256(STAGED.read_bytes()).hexdigest()
REPORT['html_bytes']=STAGED.stat().st_size
REPORT['status']='pass'
(P/'market_regime_refresh_qa.json').write_text(json.dumps(REPORT,indent=2,allow_nan=False),encoding='utf-8')
STAGED.replace(TARGET)
print('UPDATED',TARGET,flush=True)
print('ASOF',ud['asof'],'FINAL' if ud['final'] else 'INTRADAY','SCORE',REPORT['score'],flush=True)
print('MACRO DATES',json.dumps(REPORT['macro_last_dates']),flush=True)
print('OPTIONS DATES',json.dumps(REPORT['options_last_dates']),flush=True)
print('WARNINGS',json.dumps(REPORT['warnings']),flush=True)
print('BACKUP',backup,flush=True)
