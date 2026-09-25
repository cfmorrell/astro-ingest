# Group 9: split multi-night sessions. Phase "move" (fast renames) then phase "copy" (resumable flat copies, 140 s chunks).
import os,re,sys,time,shutil,datetime,json,collections
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
LOG=os.path.join(os.path.dirname(HERE),'plans','group9_split.log')
def log(m): open(LOG,'a').write(m+'\n'); print(m)
def night(fn):
    m=re.search(r'(20\d{2})-?(\d{2})-?(\d{2})[-_T](\d{2})-?(\d{2})',fn)
    return (datetime.datetime(*map(int,m.groups()))-datetime.timedelta(hours=12)).date().isoformat() if m else None
def filt(fn):
    m=re.search(r'_(?:183MM|294MC|2600MM|2600MC)_([A-Za-z]+)_gain',fn); return m.group(1) if m else None
FITS=re.compile(r'\.(fits?|xisf)$',re.I)
def mv(s,d):
    if os.path.exists(d): log(f"SKIP exists {d}"); return
    os.makedirs(os.path.dirname(d),exist_ok=True); os.rename(s,d)
CASES=[
 dict(t='PinwheelGalaxy-M101',old='2022-06-03and04-M101',names={'2022-06-03':'2022-06-03-PinwheelGalaxy-294MC-RC6','2022-06-04':'2022-06-04-PinwheelGalaxy-294MC-RC6'},home='2022-06-03',lights={'lights':'lights'},flats='split'),
 dict(t='AndromedaGalaxy-M31-NGC224',old='2021-09-11-Andromeda',names={'2021-09-04':'2021-09-04-AndromedaGalaxy-294MC-Z61','2021-09-10':'2021-09-10-AndromedaGalaxy-294MC-Z61','2021-09-11':'2021-09-11-AndromedaGalaxy-294MC-Z61'},home='2021-09-10',lights={'.':'lights'},flats=None),
 dict(t='WhirlpoolGalaxy-M51',old='2023-05-11-M51-CLR',names={'2023-05-11':'2023-05-11-WhirlpoolGalaxy-294MC-Z61','2023-05-13':'2023-05-13-WhirlpoolGalaxy-294MC-Z61'},home='2023-05-11',lights={'lights':'lights'},flats='copy'),
 dict(t='WizardNebula-NGC7380',old='2023-10-01-WizardNebula-HO-Mono',names={'2023-10-01':'2023-10-01-WizardNebula-Mono-183MM-SV503','2023-11-02':'2023-11-02-WizardNebula-Mono-183MM-SV503'},home='2023-10-01',lights={'lights':'lights'},flats='copy'),
 dict(t='Pleiades-M45',old='2023-11-03-M45-Mosaic-RGBL-Mono',names={'2023-11-02':'2023-11-02-Pleiades-Mosaic-Mono-183MM-SV503','2023-11-06':'2023-11-06-Pleiades-Mosaic-Mono-183MM-SV503'},home='2023-11-02',lights={'M45_1-1':'lights-Panel1','M45_2-1':'lights-Panel2'},flats='copy'),
 dict(t='DumbbellNebula-M27',old='2023-08-09-M27-Mono',names={'2023-08-09':'2023-08-09-DumbbellNebula-Mono-183MM-SV503','2023-08-11':'2023-08-11-DumbbellNebula-Mono-183MM-SV503','2023-08-13':'2023-08-13-DumbbellNebula-Mono-183MM-SV503','2023-08-16':'2023-08-16-DumbbellNebula-Mono-183MM-SV503'},home='2023-08-09',lights={'lights':'lights'},flats='copy'),
]
def nearest(n,nights):  # map a calibration night to the lights night on/just before it
    c=[x for x in sorted(nights) if x<=n]; return c[-1] if c else sorted(nights)[0]
def do_move():
    for c in CASES:
        t=c['t']; old=os.path.join(t,c['old']); home=os.path.join(t,c['names'][c['home']])
        if os.path.isdir(old): mv(old,home); log(f"RENAME {old} -> {home}")
        elif not os.path.isdir(home): log(f"MISSING {old}"); continue
        P={n:os.path.join(t,nm) for n,nm in c['names'].items()}
        for src,dstname in c['lights'].items():
            sd=os.path.join(home,src) if src!='.' else home
            if not os.path.isdir(sd): continue
            for f in sorted(os.listdir(sd)):
                if not FITS.search(f): continue
                n=night(f); 
                if n not in P: log(f"UNASSIGNED {sd}/{f} night={n}"); continue
                d=os.path.join(P[n],dstname,f)
                if os.path.abspath(os.path.join(sd,f))!=os.path.abspath(d): mv(os.path.join(sd,f),d)
            if src!='.' and src!=dstname and os.path.isdir(sd) and not [x for x in os.listdir(sd) if FITS.search(x)]:
                os.makedirs(os.path.join(home,'_to_delete'),exist_ok=True); mv(sd,os.path.join(home,'_to_delete','empty_'+src))
        if c['flats']=='split':
            for kind in ('flats','darkflats'):
                sd=os.path.join(home,kind)
                if not os.path.isdir(sd): continue
                for f in [f for f in os.listdir(sd) if FITS.search(f)]:
                    n=nearest(night(f),P); d=os.path.join(P[n],kind,f)
                    if os.path.abspath(d)!=os.path.abspath(os.path.join(sd,f)): mv(os.path.join(sd,f),d)
        elif c['flats']=='copy':
            allfl=[x for p in P.values() if os.path.isdir(os.path.join(p,'flats')) for x in os.listdir(os.path.join(p,'flats')) if FITS.search(x)]
            F=collections.Counter(nearest(night(x),P) for x in allfl).most_common(1)[0][0]
            for kind in ('flats','darkflats'):
                for p in P.values():
                    sd=os.path.join(p,kind)
                    if p==P[F] or not os.path.isdir(sd): continue
                    for f in [f for f in os.listdir(sd) if FITS.search(f)]: mv(os.path.join(sd,f),os.path.join(P[F],kind,f))
        for n,p in P.items(): log(f"  {p}: " + ' '.join(f"{d}={len([x for x in os.listdir(os.path.join(p,d)) if FITS.search(x)])}" for d in sorted(os.listdir(p)) if os.path.isdir(os.path.join(p,d)) and not d.startswith(('_','stacked'))))
def do_copy():
    t0=time.time()
    for c in CASES:
        if c['flats']!='copy': continue
        t=c['t']; P={n:os.path.join(t,nm) for n,nm in c['names'].items()}
        src=[p for p in P.values() if os.path.isdir(os.path.join(p,'flats')) and [x for x in os.listdir(os.path.join(p,'flats')) if FITS.search(x) and not os.path.exists(os.path.join(p,'flats','.copied_from'))]]
        src=[p for p in src if not os.path.exists(os.path.join(p,'.flats_are_copies'))]
        if not src: continue
        S=max(src,key=lambda p:len([x for x in os.listdir(os.path.join(p,'flats')) if FITS.search(x)]))
        for n,p in P.items():
            if p==S: continue
            lf={filt(x) for d in os.listdir(p) if d.startswith('lights') for x in os.listdir(os.path.join(p,d)) if FITS.search(x)}
            for kind in ('flats','darkflats'):
                sd=os.path.join(S,kind)
                if not os.path.isdir(sd): continue
                for f in sorted(os.listdir(sd)):
                    if not FITS.search(f) or filt(f) not in lf: continue
                    d=os.path.join(p,kind,f)
                    if os.path.exists(d) and os.path.getsize(d)==os.path.getsize(os.path.join(sd,f)): continue
                    if time.time()-t0>140: print('PAUSE'); return
                    os.makedirs(os.path.dirname(d),exist_ok=True); shutil.copyfile(os.path.join(sd,f),d+'.part'); os.rename(d+'.part',d)
            open(os.path.join(p,'.flats_are_copies'),'w').write(f"flats/darkflats in this session are copies from {os.path.basename(S)} (filters {sorted(x for x in lf if x)})\n")
    print('ALLDONE')
if __name__=='__main__': {'move':do_move,'copy':do_copy}[sys.argv[1]]()
