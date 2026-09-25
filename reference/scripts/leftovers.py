# Inventory processing-leftover folders (PixInsight/WBPP/Siril/etc.) with size and "keep candidates". Resumable, 140 s chunks.
import os,re,sys,time
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE)); os.chdir(ROOT)
OUT=os.path.join(os.path.dirname(HERE),'plans','group7_processing_leftovers.tsv')
PAT=re.compile(r'^(pixinsight.*|wbpp.*|autointegrate|siril|siril processing|process|masters|.*\.pxiproject|working.*|newtechnique|combinednights)$',re.I)
FINAL=re.compile(r'(\.(jpe?g|png|tiff?)$)|final|result|stretch|crop|starless|finished|export',re.I)
seen=set()
if os.path.exists(OUT): seen={tuple(l.split('\t')[:3]) for l in open(OUT).read().splitlines()[1:]}
else: open(OUT,'w').write('target\tparent\tfolder\tsize_GB\tfiles\tkeep_candidates\n')
t0=time.time()
for t in sorted(os.listdir('.')):
    if (not t[0].isalpha() or t[0]=='Z') or not os.path.isdir(t): continue
    for s in sorted(os.listdir(t)):
        S=os.path.join(t,s)
        if not os.path.isdir(S): continue
        for dp,dn,fn in os.walk(S):
            if '_to_delete' in dp.split(os.sep): dn[:]=[]; continue
            hits=[d for d in dn if PAT.match(d)]; dn[:]=[d for d in dn if d not in hits and not d.lower().startswith(('lights','flats','darkflats'))]
            for d in hits:
                key=(t,os.path.relpath(dp,t),d)
                if key in seen: continue
                if time.time()-t0>140: print('PAUSE'); sys.exit()
                P=os.path.join(dp,d); size=0; n=0; keep=[]
                for a,b,c in os.walk(P):
                    for x in c:
                        if x=='.DS_Store' or x.startswith('._'): continue
                        fp=os.path.join(a,x)
                        try: sz=os.lstat(fp).st_size
                        except: sz=0
                        size+=sz; n+=1
                        if FINAL.search(x): keep.append(f"{os.path.relpath(fp,P)} ({sz/1e6:.0f}MB)")
                with open(OUT,'a') as o: o.write(f"{key[0]}\t{key[1]}\t{d}\t{size/1e9:.1f}\t{n}\t{' | '.join(keep[:8])}{' | +'+str(len(keep)-8)+' more' if len(keep)>8 else ''}\n")
                seen.add(key)
print('ALLDONE')
