# Summarize missing / mismatched calibration frames from every PROJECT_INFO.txt
import os,re,glob,collections
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.environ.get('ASTRO_ROOT') or os.path.dirname(os.path.dirname(HERE))
need=collections.defaultdict(list)
for p in sorted(glob.glob(os.path.join(ROOT,'[A-Y]*','*','PROJECT_INFO.txt'))):
    t=open(p).read(); sess=os.path.relpath(os.path.dirname(p),ROOT)
    cam=re.search(r'master-folder name: ([^)]+)\)',t); cam=cam.group(1) if cam else '?'
    temp=re.findall(r'temp (-?[\d.]+)\.\.(-?[\d.]+)C',t)
    for m in re.finditer(r'For ([\d.?]+)s lights \(gain (\S+), offset (\S+)\):\n(.*)',t):
        e,g,o,line=m.groups()
        if 'NO MATCHING' in line: need[('DARK',cam,e,g,o,'missing')].append(sess)
        else:
            mo=re.search(r'offset (\S+) temp',line)
            if mo and o!='None' and mo.group(1)!=o: need[('DARK',cam,e,g,o,f'only offset {mo.group(1)} in library')].append(sess)
    for m in re.finditer(r'gain (\S+) offset (\S+): !! NO MATCHING MASTER BIAS',t):
        need[('BIAS',cam,'-',m.group(1),m.group(2),'missing')].append(sess)
for k in sorted(need,key=lambda k:(k[1],k[0],k[2])):
    print('|'.join(k),'|',len(need[k]),'|',' ; '.join(need[k]))
