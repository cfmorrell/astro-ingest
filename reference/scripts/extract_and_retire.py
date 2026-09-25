# For each leftover folder in the group7 plan: move keepers (masterLight*.xisf/.fit not under registered/, Working*/ TIFs)
# into <session>/stacked/, then move the folder into <session>/_to_delete/. Resumable; logs to plan .log
import os,re,sys,time,json
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
PLAN=os.path.join(os.path.dirname(HERE),'plans','group7_processing_leftovers.tsv'); LOG=PLAN.replace('.tsv','_apply.log')
done=set(l.split('\t')[1] for l in open(LOG)) if os.path.exists(LOG) else set()
t0=time.time()
for l in open(PLAN).read().splitlines()[1:]:
    t,par,folder=l.split('\t')[:3]; key=f"{t}/{par}/{folder}"
    if key in done: continue
    if time.time()-t0>140: print('PAUSE'); sys.exit()
    P=os.path.join(t,par,folder)
    if not os.path.isdir(P): open(LOG,'a').write(f"GONE\t{key}\n"); continue
    sess=os.path.join(t,par.split('/')[0]); st=os.path.join(sess,'stacked'); td=os.path.join(sess,'_to_delete')
    kept=[]
    for dp,dn,fn in os.walk(P):
        if 'registered' in dp.lower().split(os.sep): continue
        for x in fn:
            ok=(x.startswith('masterLight') and re.search(r'\.(xisf|fits?)$',x,re.I)) or (folder.lower().startswith('working') and re.search(r'\.tiff?$',x,re.I))
            if not ok: continue
            os.makedirs(st,exist_ok=True); dst=os.path.join(st,x)
            if os.path.exists(dst): dst=os.path.join(st,folder.replace(' ','_')+'__'+x)
            if os.path.exists(dst): continue
            os.rename(os.path.join(dp,x),dst); kept.append(os.path.basename(dst))
    os.makedirs(td,exist_ok=True); name=(par.split('/',1)[1].replace('/','_')+'_' if '/' in par else '')+folder
    dst=os.path.join(td,name)
    if os.path.exists(dst): open(LOG,'a').write(f"SKIPEXISTS\t{key}\n"); continue
    os.rename(P,dst)
    open(LOG,'a').write(f"OK\t{key}\t-> {os.path.relpath(dst,sess)}\tkept={len(kept)}\t{json.dumps(kept)}\n")
print('ALLDONE')
