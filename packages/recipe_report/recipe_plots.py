"""Shared local chart rendering for the private report, no upload code."""
from pathlib import Path
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

from composition_chart import render_composition
from recipe_display import NAMES, PROPERTIES, component_groups
from recipe_labels import preferred_label


def configure_font():
    candidates=[Path('/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf'),
                Path('C:/Windows/Fonts/msyh.ttc')]
    for path in candidates:
        if path.is_file():
            font_manager.fontManager.addfont(str(path))
            plt.rcParams['font.family']=['DejaVu Sans',font_manager.FontProperties(fname=str(path)).get_name()]
            break
    plt.rcParams.update({'axes.unicode_minus':False,'font.size':11,'figure.dpi':160})


def display_coordinate(key,value):
    if key.endswith('_MPa') and key[0] in {'E','G'}:return value/1000
    if key in {'phase_risk','fiber_volume_fraction'}:return value*100
    return value


def tradeoff(report,path):
    glass = report['domain'] == 'chip_glass_thermomechanical_family_v1'
    if glass:
        from glass_presentation import property_label
    else:
        property_label = lambda key: PROPERTIES[key][0]
    near=not report['candidates'] and bool(report.get('nearest_candidates'))
    selected=(report['candidates'] or report.get('nearest_candidates',[]))[0];goals=report['objectives']
    history=[r for r in report['history'] if r.get('admissible') is True] if near else [r for r in report['history'] if r.get('feasible') is True]
    keys=[g['property'] for g in goals]
    fig,ax=plt.subplots(figsize=(8.6,4.6),layout='constrained')
    if len(keys)>1:
        x,y=keys[:2]
        ax.scatter([display_coordinate(x,r['values'][x]) for r in history],
                   [display_coordinate(y,r['values'][y]) for r in history],s=18,color='#C7D0D9',label='已评估有效配方' if near else '已评估可行配方')
        front=report['front']
        if front:
            args={'s':35,'color':'#326B9B','label':'Pareto权衡方案'}
            if len(keys)>2:
                z=keys[2];args.pop('color');args.update(c=[display_coordinate(z,r['values'][z]) for r in front],cmap='viridis')
            sc=ax.scatter([display_coordinate(x,r['values'][x]) for r in front],
                          [display_coordinate(y,r['values'][y]) for r in front],**args)
            if len(keys)>2:fig.colorbar(sc,ax=ax,label=property_label(z)+' / '+PROPERTIES[z][1])
        ax.scatter(display_coordinate(x,selected['values'][x]),display_coordinate(y,selected['values'][y]),
                   marker='*',s=170,color='#B96642',edgecolor='white',label=preferred_label(report),zorder=5)
        ax.set(xlabel=property_label(x)+' / '+PROPERTIES[x][1],ylabel=property_label(y)+' / '+PROPERTIES[y][1])
        title='性能差距与接近方案' if near else '目标性质权衡' if report['method']=='nsga2' else '已记录组分的输运响应比较'
    else:
        key=keys[0]
        ordered=report['candidates'] or report.get('nearest_candidates',[])
        ax.bar(range(len(ordered)),[display_coordinate(key,r['values'][key]) for r in ordered],color='#326B9B',label='目标性质预测值')
        ax.set(xlabel='接近方案序号' if near else '优选方案序号',ylabel=PROPERTIES[key][0]+' / '+PROPERTIES[key][1])
        title='接近目标的方案比较' if near else '单目标方案比较'
        ax.set_xticks(range(len(ordered)), [row['display_id'] for row in ordered])
        ax.set(xlabel='配方方案', ylabel=property_label(key)+' / '+PROPERTIES[key][1])
        if goals[0]['mode'] == 'target':
            ax.axhline(display_coordinate(key,goals[0]['target']), color='#B96642', linestyle='--', label='指定目标值')
    if glass:
        title='玻璃配方的目标性能比较' if not near else '玻璃配方的性能差距比较'
    ax.set_title(title);ax.grid(alpha=.14);ax.legend(frameon=False,fontsize=9)
    fig.savefig(path,facecolor='white',dpi=160);plt.close(fig)


def render_assets(report,directory):
    configure_font();directory=Path(directory);assets=[]
    candidates=report['candidates'] or report.get('nearest_candidates',[])
    if not candidates:return assets
    def asset(name,title):
        filename=name+'.png'
        return {'name':name,'filename':filename,'title':title,'path':str(directory/filename),
                'url':str((directory/filename).resolve()).replace('\\','/')}
    for index,(group,c,unit) in enumerate(component_groups(report,candidates[0])):
        item=asset(f'recipe_composition_{index}',group+'比例')
        if report['domain'] == 'chip_glass_thermomechanical_family_v1':
            from glass_presentation import OXIDE_NAMES, SUBSCRIPTS
            names = {k.translate(SUBSCRIPTS): v for k,v in OXIDE_NAMES.items()}
            display_c = {k.translate(SUBSCRIPTS): v for k,v in c.items()}
            item['title'] = preferred_label(report)+'的组分含量'
            render_composition(display_c,directory/item['filename'],names=names,unit=unit,title=item['title']+'（'+unit+'）')
        else:
            item['title'] = preferred_label(report)+'的'+group
            render_composition(c,directory/item['filename'],names=NAMES,unit=unit,title=item['title']+'（'+unit+'）')
        assets.append(item)
    item=asset('recipe_tradeoff','目标性质比较')
    tradeoff(report,directory/item['filename']);assets.append(item)
    return assets
