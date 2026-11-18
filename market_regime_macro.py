"""Nasdaq versus MOVE comparison, shared by the dashboard installer and data refresh."""
import json,math,re
INDICES={'composite':('Nasdaq Composite','CCMP Index'),'nasdaq100':('Nasdaq-100','NDX Index')}
TICKERS=['CCMP Index','NDX Index','MOVE Index']
def build_data(history,fetched):
 def points(ticker):
  return {d:float(r['PX_LAST']) for d,r in history.get(ticker,{}).items() if isinstance(r.get('PX_LAST'),(int,float)) and math.isfinite(r['PX_LAST']) and r['PX_LAST']>0}
 move=points('MOVE Index')
 if len(move)<252:raise ValueError('Insufficient Bloomberg MOVE history')
 out={'source':'Bloomberg Desktop API, daily PX_LAST','fetched':fetched,'move_name':'ICE BofA MOVE Index','move_ticker':'MOVE Index','indices':{}}
 for key,(name,ticker) in INDICES.items():
  nasdaq=points(ticker);dates=sorted(nasdaq.keys() & move.keys())
  if len(dates)<252:raise ValueError('Insufficient matched Nasdaq/MOVE observations: '+ticker)
  out['indices'][key]={'name':name,'ticker':ticker,'d':dates,'nasdaq':[nasdaq[d] for d in dates],'move':[move[d] for d in dates],'nasdaq_last_date':max(nasdaq),'move_last_date':max(move)}
  out['indices'][key]['relationship']=build_relationship(history,ticker,dates)
 return out

def build_relationship(history, nasdaq_ticker, dates):
 """Pair close-to-close changes only when both indices share the same start and end dates."""
 import numpy as np
 import pandas as pd
 def raw(ticker):
  return pd.Series({d:float(row['PX_LAST']) for d,row in history[ticker].items()
   if isinstance(row.get('PX_LAST'),(int,float)) and math.isfinite(row['PX_LAST']) and row['PX_LAST']>0}).sort_index()
 n,m=raw(nasdaq_ticker),raw('MOVE Index')
 nprevious=pd.Series(n.index,index=n.index).shift()
 mprevious=pd.Series(m.index,index=m.index).shift()
 paired=pd.DataFrame({'n':n.pct_change(fill_method=None),'m':m.pct_change(fill_method=None),
                      'np':nprevious,'mp':mprevious}).reindex(dates)
 paired=paired.loc[(paired['np']==paired['mp']) & paired['n'].notna() & paired['m'].notna()].copy()
 product=paired['n']*paired['m']
 eligible=product.ne(0).astype(int)
 same=product.gt(0).astype(int)
 def array(series):
  return [None if pd.isna(v) else float(v) for v in series.reindex(dates)]
 signs={day:('both_up' if row['n']>0 and row['m']>0 else
             'both_down' if row['n']<0 and row['m']<0 else
             'flat' if row['n']==0 or row['m']==0 else 'opposite')
        for day,row in paired.iterrows()}
 out={'nasdaq_change':array(paired['n']),'move_change':array(paired['m']),
      'direction':[signs.get(day) for day in dates],'windows':{},
      'history':{'paired_changes':len(paired),'correlation':float(paired['n'].corr(paired['m'])),
                 'same_direction_share':float(same.sum()/eligible.sum()) if eligible.sum() else None}}
 for size in [20,60,120]:
  corr=paired['n'].rolling(size,min_periods=size).corr(paired['m']).clip(-1,1)
  denom=eligible.rolling(size,min_periods=size).sum()
  share=same.rolling(size,min_periods=size).sum()/denom.replace(0,np.nan)
  starts=pd.Series(list(paired.index),index=paired.index).shift(size-1)
  out['windows'][str(size)]={'correlation':array(corr),'same_direction_share':array(share),
      'directional_count':array(denom),'start_date':[None if pd.isna(v) else str(v) for v in starts.reindex(dates)]}
  # Independently reconcile the latest computed sample against direct NumPy arithmetic.
  if len(paired)>=size:
   sample=paired.iloc[-size:]
   expected=float(np.corrcoef(sample['n'].to_numpy(),sample['m'].to_numpy())[0,1])
   assert math.isclose(corr.iloc[-1],expected,rel_tol=1e-9,abs_tol=1e-9)
   products=sample['n'].to_numpy()*sample['m'].to_numpy()
   count=int(np.count_nonzero(products))
   expected_share=float(np.count_nonzero(products>0)/count) if count else None
   if expected_share is not None:assert math.isclose(share.iloc[-1],expected_share,abs_tol=1e-12)
 # Meaningful synthetic cases catch sign inversion, flat-day handling and zero-variance correlation.
 x=pd.Series([.01,-.02,.03,-.01,.02])
 assert math.isclose(x.corr(x),1,abs_tol=1e-12)
 assert math.isclose(x.corr(-x),-1,abs_tol=1e-12)
 assert pd.isna(x.corr(pd.Series([.01]*5)))
 return out

SECTION=r"""
<!-- BEGIN MARCO PAGE -->
<div class="page" id="pg-macro">
<h3>Nasdaq versus MOVE Index</h3>
<p class="sub">Equity index levels alongside bond-market volatility. Choose a Nasdaq index and a window to compare their histories.</p>
<div class="bar"><span class="seg" id="macroIndex"><button data-index="composite">Nasdaq Composite</button><button data-index="nasdaq100">Nasdaq-100</button></span></div>
<div class="bar"><span class="seg" id="macroPer"></span><span class="seg" id="macroMode"><button data-mode="levels">Separate axes</button><button data-mode="indexed">Indexed to 100</button></span></div>
<div class="cards" id="macroCards"></div>
<div class="panel">
 <div class="ptitle" id="macroTitle"></div>
 <div class="pnote" id="macroWindow"></div>
 <div class="legend" id="macroLegend"></div>
 <div id="macroChart" data-h="390"></div>
 <div class="pnote" id="macroAxis"></div>
</div>

<div class="panel" id="macroRelationshipPanel">
 <div class="ptitle">Do Nasdaq and MOVE move together?</div>
 <div class="pnote">Correlation of matched daily percentage changes, rather than index levels. Above zero = positive co-movement; below zero = inverse co-movement. Values near zero indicate a weak linear relationship.</div>
 <div class="bar"><span>Lookback:</span><span class="seg" id="macroLookback"><button data-lookback="20">20 observations</button><button data-lookback="60">60 observations</button><button data-lookback="120">120 observations</button></span></div>
 <div class="cards" id="macroRelationshipCards"></div>
 <div class="legend"><span><i style="background:var(--r)"></i>Rolling correlation</span><span>+1 = perfectly together | 0 = no linear correlation | -1 = perfectly opposite</span></div>
 <div id="macroCorrelation" data-h="250"></div>
 <div class="pnote" id="macroCorrelationNote"></div>
</div>
<div class="panel">
 <div class="ptitle">How often do they move in the same direction?</div>
 <div class="pnote">Share of directional days in the same rolling sample when both rise or both fall. Above 50% means same-direction days are more frequent; below 50% means opposite-direction days are more frequent. The 50% line is a count guide, not a significance threshold.</div>
 <div id="macroDirection" data-h="210"></div>
 <div class="pnote" id="macroDirectionNote"></div>
</div>

<p class="pnote" id="macroSource"></p>
<p class="pnote">MOVE measures bond-market volatility; it is not an equity index or a return series. Independent axis scales do not imply equal percentage changes or a causal relationship. <a href="https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-move-index" target="_blank" rel="noopener noreferrer">ICE MOVE methodology and overview</a>.</p>
</div>
<!-- END MARCO PAGE -->
"""
JS=r"""
// BEGIN MARCO JS
const MACRO=ALL.macro;
const MACRO_PER=[["1M",1,0],["3M",3,0],["6M",6,0],["1Y",0,1],["3Y",0,3],["5Y",0,5],["10Y",0,10],["Max",0,0]];
let macroIndex="composite",macroPer="3Y",macroMode="levels";
const macroFormat=v=>v==null?"Unavailable":v.toLocaleString("en-US",{maximumFractionDigits:2});

let macroLookback="60";
const macroPercent=v=>v==null?"Unavailable":(v*100).toFixed(1)+"%";
const macroChange=v=>v==null?"Unavailable":(v>=0?"+":"")+(v*100).toFixed(2)+"%";
const macroDirectionName=key=>({both_up:"Both rose",both_down:"Both fell",opposite:"Opposite moves",flat:"At least one index unchanged"})[key]||"No matching one-day change";
function renderMacroRelationship(data,ctx){
 const rel=data.relationship,w=rel?.windows?.[macroLookback];
 if(!w)return;
 const {A,B,d}=ctx,cor=w.correlation[B],same=w.same_direction_share[B],sign=rel.direction[B];
 const correlationLabel=cor==null?"Insufficient observations":cor>0?"Positive correlation":cor<0?"Negative correlation":"Zero correlation";
 const tip=i=>'<br>Nasdaq daily change: '+macroChange(rel.nasdaq_change[i])+'<br>MOVE daily change: '+macroChange(rel.move_change[i])+'<br>'+macroDirectionName(rel.direction[i]);
 const usable=(values)=>values.slice(A,B+1).some(Number.isFinite);
 if(usable(w.correlation)){
  chart(document.getElementById("macroCorrelation"),ctx,
   [{name:macroLookback+"-observation correlation",c:"--r",v:w.correlation,fmt:v=>v.toFixed(3)}],
   v=>v.toFixed(1),0,[{v:1,c:"--muted",label:"+1 together"},{v:0,c:"--muted",label:"0"},{v:-1,c:"--muted",label:"-1 opposite"}],tip);
 }else document.getElementById("macroCorrelation").textContent="Not enough matched observations in this display window.";
 const sharePercent=w.same_direction_share.map(v=>v==null?null:v*100);
 if(usable(sharePercent)){
  chart(document.getElementById("macroDirection"),ctx,
   [{name:"Same-direction days",c:"--q",v:sharePercent,fmt:v=>v.toFixed(1)+"%"}],
   v=>v.toFixed(0)+"%",50,[{v:0,c:"--muted",label:"0%"},{v:50,c:"--muted",label:"50% equal count"},{v:100,c:"--muted",label:"100%"}],tip);
 }else document.getElementById("macroDirection").textContent="Not enough matched directional days in this display window.";
 const date=d[B],sample=w.start_date[B];
 const card=(label,value,note,color)=>'<div class="card"><div class="k">'+label+'</div><div class="v"'+(color?' style="color:var('+color+')"':'')+'>'+value+'</div><div class="small">'+note+'</div></div>';
 document.getElementById("macroRelationshipCards").innerHTML=
  card(macroLookback+"-observation correlation",cor==null?"Unavailable":(cor>=0?"+":"")+cor.toFixed(2),correlationLabel,"--r")+
  card("Same-direction days",macroPercent(same),same==null?"Unavailable":macroPercent(1-same)+" opposite; unchanged days excluded","--q")+
  card("Latest matched daily moves",macroDirectionName(sign),"Nasdaq "+macroChange(rel.nasdaq_change[B])+" | MOVE "+macroChange(rel.move_change[B]),"--ink");
 document.getElementById("macroCorrelationNote").textContent="Latest reading: "+date+(sample?"; sample "+sample+" to "+date:"")+". Pearson correlation uses the last "+macroLookback+" valid paired daily changes, calculated on full history before selecting the display window. A Nasdaq change is paired with MOVE only when both changes have the same start and end dates. Missing changes create gaps; zero-variance samples have no correlation. Correlation sign describes a tendency, not a rule for every day or a forecast.";
 document.getElementById("macroDirectionNote").textContent="Latest directional denominator: "+(w.directional_count[B]==null?"unavailable":w.directional_count[B].toFixed(0))+" out of "+macroLookback+" matched changes. Both up and both down count as same direction; one up and one down count as opposite. Flat days are excluded from this percentage but remain in the correlation sample. Historical baseline ("+rel.history.paired_changes.toLocaleString()+" matched changes): correlation "+rel.history.correlation.toFixed(3)+", same-direction share "+macroPercent(rel.history.same_direction_share)+". Hover either chart to see the underlying daily changes.";
 document.querySelectorAll("#macroLookback button").forEach(button=>button.classList.toggle("on",button.dataset.lookback===macroLookback));
 for(const id of ["macroCorrelation","macroDirection"]){
  const el=document.getElementById(id);el.dataset.lookback=macroLookback;el.dataset.index=macroIndex;
 }
 document.getElementById("macroCorrelation").dataset.latest=cor==null?"":String(cor);
 document.getElementById("macroDirection").dataset.latest=same==null?"":String(same);
}

function renderMacro(){
 const data=MACRO?.indices?.[macroIndex],el=document.getElementById("macroChart");
 if(!data){el.textContent="Nasdaq / MOVE history is unavailable.";return;}
 const d=data.d,B=d.length-1,period=MACRO_PER.find(p=>p[0]===macroPer)||MACRO_PER[4];
 const cutoff=period[0]==="Max"?d[0]:shift(d[B],period[1],period[2]);
 let A=d.findIndex(date=>date>=cutoff);if(A<0)A=0;if(A>=B)A=Math.max(0,B-1);
 const indexed=macroMode==="indexed";
 const nv=indexed?data.nasdaq.map(v=>v/data.nasdaq[A]*100):data.nasdaq;
 const mv=indexed?data.move.map(v=>v/data.move[A]*100):data.move;
 const ctx={d,A,B};
 const foreground=[{name:data.name+(indexed?" (rebased)":" (left axis)"),c:"--q",v:nv,fmt:macroFormat}];
 const background=indexed?null:{name:"MOVE (right axis)",v:mv,fmt:macroFormat};
 if(indexed)foreground.push({name:"MOVE (rebased)",c:"--s",v:mv,fmt:macroFormat});
 chart(el,ctx,foreground,macroFormat,indexed?100:null,[],null,background);
 if(!indexed){
  const paths=el.querySelectorAll("svg > path");
  if(paths[0]){paths[0].setAttribute("fill",css("--s"));paths[0].setAttribute("fill-opacity",".07");}
  if(paths[1]){paths[1].setAttribute("stroke",css("--s"));paths[1].setAttribute("stroke-width","2");}
 }
 el.dataset.index=macroIndex;el.dataset.period=macroPer;el.dataset.mode=macroMode;el.dataset.first=d[A];el.dataset.last=d[B];
 document.getElementById("macroTitle").textContent=data.name+" versus ICE BofA MOVE Index";
 document.getElementById("macroWindow").textContent=d[A]+" to "+d[B]+" | "+(B-A+1).toLocaleString()+" matched daily observations";
 document.getElementById("macroLegend").innerHTML='<span><i style="background:var(--q)"></i>'+data.name+(indexed?' (rebased to 100)':' (left axis, index points)')+'</span><span><i style="background:var(--s)"></i>MOVE'+(indexed?' (rebased to 100)':' (right axis, index points)')+'</span>';
 document.getElementById("macroAxis").textContent=indexed?"Both series start at 100 on the first selected date. MOVE changes describe changes in its volatility index, not an investment return.":"Left axis: "+data.name+" index level. Right axis: MOVE index level. Both axes increase upwards and rescale to the selected window.";
 document.getElementById("macroCards").innerHTML='<div class="card"><div class="k">'+data.name+'</div><div class="v" style="color:var(--q)">'+macroFormat(data.nasdaq[B])+'</div><div class="small">Matched observation '+d[B]+'</div></div><div class="card"><div class="k">MOVE Index</div><div class="v" style="color:var(--s)">'+macroFormat(data.move[B])+'</div><div class="small">Matched observation '+d[B]+'</div></div>';
 document.getElementById("macroSource").textContent="Source: "+MACRO.source+". "+data.ticker+" and MOVE Index. Retrieved "+MACRO.fetched+". Plot uses same-date observations only, with no forward filling. Latest source dates: "+data.name+" "+data.nasdaq_last_date+"; MOVE "+data.move_last_date+". Index levels exclude dividends; the latest source observation may be intraday.";
 renderMacroRelationship(data,ctx);
 for(const [id,attribute,current] of [["macroIndex","index",macroIndex],["macroPer","period",macroPer],["macroMode","mode",macroMode]])
  document.querySelectorAll("#"+id+" button").forEach(b=>b.classList.toggle("on",b.dataset[attribute]===current));
}
(function setupMacro(){
 document.getElementById("macroLookback").addEventListener("click",event=>{
  const button=event.target.closest("button");if(!button)return;
  macroLookback=button.dataset.lookback;renderMacro();
 });

 document.getElementById("macroPer").innerHTML=MACRO_PER.map(p=>'<button data-period="'+p[0]+'">'+p[0]+'</button>').join("");
 for(const [id,attribute] of [["macroIndex","index"],["macroPer","period"],["macroMode","mode"]])
  document.getElementById(id).addEventListener("click",event=>{
   const button=event.target.closest("button");if(!button)return;
   if(attribute==="index")macroIndex=button.dataset.index;
   if(attribute==="period")macroPer=button.dataset.period;
   if(attribute==="mode")macroMode=button.dataset.mode;
   renderMacro();
  });
})();
// END MARCO JS
"""
def install(page):
 page=page.replace('data-pg="marco"','data-pg="macro"').replace('id="pg-marco"','id="pg-macro"').replace('>Marco</button>','>Macro</button>')
 page=page.replace('if(k==="marco") renderMacro();','if(k==="macro") renderMacro();')

 if 'id="pg-macro"' not in page:
  marker='<button data-pg="regime">Risk regime</button>'
  if marker not in page:raise ValueError('Risk regime navigation marker not found')
  page=page.replace(marker,marker+'<button data-pg="macro">Macro</button>',1)
  marker='<div class="page" id="pg-ratios">'
  if marker not in page:raise ValueError('Ratios page marker not found')
  page=page.replace(marker,SECTION+'\n'+marker,1)
  page=page.replace('function showPage(k){','function showPage(k){\n  if(k==="macro") renderMacro();',1)
  marker='matchMedia("(prefers-color-scheme: dark)").addEventListener("change",'
  if marker not in page:raise ValueError('Theme listener marker not found')
  page=page.replace(marker,JS+'\n'+marker,1)
  page=page.replace('renderOptions(); renderPE(); });','renderOptions(); renderPE(); renderMacro(); });',1)
 else:
  page,n=re.subn(r'<!-- BEGIN MARCO PAGE -->.*?<!-- END MARCO PAGE -->',lambda m:SECTION.strip(),page,count=1,flags=re.S)
  if n!=1:raise ValueError('Marco page markers missing')
  page,n=re.subn(r'// BEGIN MARCO JS.*?// END MARCO JS',lambda m:JS.strip(),page,count=1,flags=re.S)
  if n!=1:raise ValueError('Marco JS markers missing')
 button='<button data-pg="macro">Macro</button>'
 if button in page:
  page=page.replace(button,'')
  anchor='<button data-pg="regime">Risk regime</button>'
  page=page.replace(anchor,anchor+button,1)
 assert page.count('id="pg-macro"')==1
 assert page.count('data-pg="macro"')==1
 return page
