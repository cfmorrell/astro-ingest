import os,re,sys,json,collections
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,HERE); from fitshdr import header
R=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE)); FITS=re.compile(r'\.fits?$',re.I)
SKIP=re.compile(r'^(_to_delete|process|masters|siril.*|wbpp.*|pixinsight.*|autointegrate|session \d+|dss.*|logs?|trash|badframes|.*flat.*|dark.*|bias.*)$',re.I)
out=[]
for t in sorted(os.listdir(R)):
    if (not t[0].isalpha() or t[0]=='Z') or not os.path.isdir(os.path.join(R,t)): continue
    for s in sorted(os.listdir(os.path.join(R,t))):
        S=os.path.join(R,t,s)
        if not os.path.isdir(S): continue
        pick=None
        for dp,dn,fn in os.walk(S):
            dn[:]=sorted(d for d in dn if not SKIP.match(d))
            c=[x for x in sorted(fn) if FITS.search(x) and not os.path.islink(os.path.join(dp,x)) and not x.lower().startswith(('stacked','master','result','starless','starmask','autosave'))]
            if c: pick=os.path.join(dp,c[len(c)//2]); break
        if not pick: out.append(dict(s=f"{t}/{s}")); continue
        h=header(pick)
        keep={k:h.get(k) for k in ('INSTRUME','TELESCOP','FOCALLEN','APTDIA','FOCRATIO','XPIXSZ','CREATOR','SWCREATE','PROGRAM','GUIDECAM','FILTER','GAIN','OFFSET','SITELAT','SITELONG','DATE-OBS','IMAGETYP','OBJECT','EXPTIME','CCD-TEMP','SET-TEMP','XBINNING','BAYERPAT','FWHEEL','FOCNAME','ROTNAME','ROWORDER') if h.get(k)}
        keep['s']=f"{t}/{s}"; keep['file']=os.path.relpath(pick,S); out.append(keep)
json.dump(out,open(os.path.join(HERE,'survey.json'),'w'),indent=1)
for o in out: print(json.dumps(o))
