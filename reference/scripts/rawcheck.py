# For each processing-leftover folder in group7 plan: find raw capture files inside it that do NOT exist
# (same name) elsewhere in the session outside leftover folders. Also list keep-files (masterLight*, Working TIF/FITS).
import os,re,sys,time,json
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
PLAN=os.path.join(os.path.dirname(HERE),'plans','group7_processing_leftovers.tsv'); OUT=PLAN.replace('.tsv','_rawcheck.jsonl')
PAT=re.compile(r'^(pixinsight.*|wbpp.*|autointegrate|siril|siril processing|process|masters|.*\.pxiproject|working.*|newtechnique|combinednights|_to_delete|stacked)$',re.I)
RAW=re.compile(r'\.(fits?|xisf|cr2)$',re.I); DERIV=re.compile(r'(_c|_cc|_d|_r|_a|_n|_rn|_ca|_pp|_c_d|_drz|_lps)(_[a-z]+)*\.(fits?|xisf)$|^(r_|pp_|c_|masterLight|masterFlat|masterDark|masterBias|Integration|drizzle)',re.I)
done=set()
if os.path.exists(OUT): done={json.loads(l)['key'] for l in open(OUT)}
rows=[l.split('\t') for l in open(PLAN).read().splitlines()[1:]]
t0=time.time(); idxcache={}
for t,par,folder,*_ in rows:
    key=f"{t}/{par}/{folder}"
    if key in done: continue
    P=os.path.join(t,par,folder)
    if not os.path.isdir(P): continue
    if time.time()-t0>130: print('PAUSE'); sys.exit()
    sess=os.path.join(t,par.split('/')[0])
    if sess not in idxcache:
        names=set()
        for dp,dn,fn in os.walk(sess):
            dn[:]=[d for d in dn if not PAT.match(d)]
            names.update(fn)
        idxcache[sess]=names
    orphans=[];keep=[];nraw=0
    for dp,dn,fn in os.walk(P):
        for x in fn:
            if x.startswith('masterLight') or (os.path.basename(P).lower().startswith('working') and re.search(r'\.(tiff?|fits?|xisf)$',x,re.I)):
                keep.append(os.path.relpath(os.path.join(dp,x),P))
            if RAW.search(x) and not DERIV.search(x):
                nraw+=1
                if x not in idxcache[sess]: orphans.append(os.path.relpath(os.path.join(dp,x),P))
    with open(OUT,'a') as o: o.write(json.dumps(dict(key=key,rawlike=nraw,orphans=len(orphans),orphan_sample=orphans[:5],keep=keep))+'\n')
print('ALLDONE')
