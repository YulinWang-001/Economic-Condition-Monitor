"""Install the Marco tab and connect it to the normal Bloomberg refresh."""
from pathlib import Path
import datetime as dt,json,runpy,shutil,sys,time
from zoneinfo import ZoneInfo
import blpapi
sys.stdout.reconfigure(encoding='utf-8')
P=Path(__file__).resolve().parent
refresh=P/'refresh_market_regime.py'
code=refresh.read_text(encoding='utf-8')
stamp=dt.datetime.now(ZoneInfo('Europe/London')).strftime('%Y%m%d_%H%M%S')
backup=P/'backups'/('marco_tab_'+stamp);backup.mkdir(parents=True,exist_ok=True)
for name in ['Market_Regime_Dashboard.html','refresh_market_regime.py','market_regime_bloomberg_latest.json']:
 shutil.copy2(P/name,backup/name)
if 'import market_regime_macro as macro_tools' not in code:
 code=code.replace("import market_regime_html as us","import market_regime_html as us\nimport market_regime_macro as macro_tools",1)
 code=code.replace("|{'SKEW Index'})","|{'SKEW Index','CCMP Index','NDX Index','MOVE Index'})",1)
 code=code.replace("(other,['PX_LAST'],'19990310',None)","(other,['PX_LAST'],'19900101',None)",1)
 line="page=replace_data(template,'ALL',ud);page=replace_data(page,'PE_DATA',upe)"
 assert line in code
 code=code.replace(line,"ud['macro']=macro_tools.build_data(R['history'],R.get('macro_fetched',FETCH))\n"+line+"\npage=macro_tools.install(page)",1)
 code=code.replace("['regime','vol','ratios','sectorperformance','options','momentum','pe']","['regime','vol','ratios','sectorperformance','options','momentum','pe','marco']",1)
 marker="   if(key==='momentum'){"
 assert marker in code
 code=code.replace(marker,r"""
   if(key==='marco'){
    result.macroControls=0;
    for(const index of ['composite','nasdaq100']){
     document.querySelector('#macroIndex button[data-index="'+index+'"]').click();
     for(const period of ['1M','3M','6M','1Y','3Y','5Y','10Y','Max']){
      document.querySelector('#macroPer button[data-period="'+period+'"]').click();
      for(const mode of ['levels','indexed']){
       document.querySelector('#macroMode button[data-mode="'+mode+'"]').click();await wait(10);
       const plot=document.getElementById('macroChart');
       check(plot.dataset.index===index&&plot.dataset.period===period&&plot.dataset.mode===mode,'Marco control mismatch');
       check(plot.querySelectorAll('svg > path').length>=(mode==='levels'?3:2),'Missing Nasdaq/MOVE series');
       check(!/NaN|Infinity/.test(plot.innerHTML),'Invalid Marco numeric output');
       check(document.getElementById('macroSource').textContent.includes('same-date observations'),'Missing Marco source note');
       result.macroControls++;
      }
     }
    }
    document.querySelector('#macroIndex button[data-index="composite"]').click();
    document.querySelector('#macroPer button[data-period="3Y"]').click();
    document.querySelector('#macroMode button[data-mode="levels"]').click();
    const hit=document.querySelector('#macroChart .hit'),box=hit.getBoundingClientRect();
    hit.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:box.x+box.width/2,clientY:box.y+box.height/2}));
    check(document.getElementById('tip').textContent.includes('MOVE'),'MOVE hover missing');
    check(document.getElementById('tip').textContent.includes('Nasdaq'),'Nasdaq hover missing');
    hit.dispatchEvent(new MouseEvent('mouseleave',{bubbles:true}));
   }
"""+marker,1)
 code=code.replace("testpage.write_text(tested,encoding='utf-8')","testpage.write_text(tested,encoding='utf-8')\nREPORT['macro_tab']={key:{'first':record['d'][0],'last':record['d'][-1],'observations':len(record['d']),'nasdaq_source_last':record['nasdaq_last_date'],'move_source_last':record['move_last_date']} for key,record in ud['macro']['indices'].items()}",1)
 code=code.replace("if mobile:args.append('--force-dark-mode')","args.append('--screenshot='+str(backup/('Marco_mobile.png' if mobile else 'Marco_desktop.png')))\n if mobile:args.append('--force-dark-mode')",1)
compile(code,str(refresh),'exec')
assert "'CCMP Index','NDX Index','MOVE Index'" in code
assert "ud['macro']=macro_tools.build_data" in code
refresh.write_text(code,encoding='utf-8')
cache=P/'market_regime_bloomberg_latest.json'
data=json.loads(cache.read_text(encoding='utf-8'))
end=dt.datetime.now(ZoneInfo('Europe/London')).date()
assert data['requested_end']==str(end),'Refresh the base snapshot for the current date first'
fetched=dt.datetime.now(dt.timezone.utc).isoformat()
s=blpapi.Session();assert s.start()
try:
 assert s.openService('//blp/refdata')
 q=s.getService('//blp/refdata').createRequest('HistoricalDataRequest')
 tickers=['CCMP Index','NDX Index','MOVE Index']
 for ticker in tickers:q.getElement('securities').appendValue(ticker)
 q.getElement('fields').appendValue('PX_LAST')
 q.set('startDate','19900101');q.set('endDate',end.strftime('%Y%m%d'));q.set('periodicitySelection','DAILY')
 s.sendRequest(q);deadline=time.monotonic()+180
 for ticker in tickers:data['history'][ticker]={}
 while True:
  event=s.nextEvent(5000)
  for message in event:
   if message.hasElement('responseError'):raise RuntimeError(str(message))
   if not message.hasElement('securityData'):continue
   sd=message.getElement('securityData');ticker=sd.getElementAsString('security')
   if sd.hasElement('securityError'):raise RuntimeError(str(sd.getElement('securityError')))
   assert sd.getElement('fieldExceptions').numValues()==0,str(sd)
   for row in sd.getElement('fieldData').values():
    if row.hasElement('PX_LAST') and not row.getElement('PX_LAST').isNull():
     day=str(row.getElementAsDatetime('date'))[:10]
     data['history'][ticker][day]={'PX_LAST':row.getElementAsFloat('PX_LAST')}
  if event.eventType()==blpapi.Event.RESPONSE:break
  if time.monotonic()>deadline:raise TimeoutError('Nasdaq/MOVE historical request timed out')
finally:s.stop()
data['macro_fetched']=fetched
data['requests'].append({'securities':tickers,'fields':['PX_LAST'],'start':'19900101','end':str(end),'fetched':fetched,'basis':'price index levels'})
from market_regime_macro import build_data
preview=build_data(data['history'],fetched)
print('Nasdaq / MOVE matched history',json.dumps({key:{'first':v['d'][0],'last':v['d'][-1],'observations':len(v['d'])} for key,v in preview['indices'].items()}),flush=True)
tmp=cache.with_suffix('.json.tmp')
tmp.write_text(json.dumps(data,separators=(',',':'),allow_nan=False),encoding='utf-8')
tmp.replace(cache)
sys.argv=[str(refresh),'--reuse']
runpy.run_path(str(refresh),run_name='__main__')
print('MARCO TAB INSTALLED; original dashboard and refresh script backed up at',backup,flush=True)
