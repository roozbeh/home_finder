"""Active listing exploration and transparent, user-entered flip scenarios."""
import math
from collections import defaultdict
from flask import Blueprint, render_template, request, abort

from mls_geography import BAY_COUNTIES

CANDIDATES = {'SF426123960':1417626,'ML82065431':1450312,'41149750':1476152,'BA326084425':794430,'CRIV26238559':1085427,'CRSN26222098':1096760,'41149039':1278476,'ML82066483':1234969,'41152317':1133491,'BA326091746':524948}

def renovation_bullets(assessment):
    """Keep saved observations expandable until they have structured costs."""
    return [{
        'area': observation.get('area') or 'Other work',
        'detail': observation.get('detail') or '',
    } for observation in assessment.get('observations', []) if isinstance(observation, dict)]


def purchase_recommendation(assessment, asking):
    # Use assessed values only: the unadjusted candidate screen is not an ARV.
    keys = ('resale_midpoint','renovation_low','renovation_high','hoa_monthly')
    if any(not isinstance(assessment.get(k),(int,float)) or isinstance(assessment.get(k),bool) or not math.isfinite(assessment[k]) or assessment[k]<0 for k in keys):
        return None
    resale, low, high, hoa = (assessment[k] for k in keys)
    if resale <= 0 or high < low: return None
    def ceiling(repairs, resale_value=resale): return (resale_value*.935-repairs-hoa*10)/1.1025
    resale_low = assessment.get('resale_low') or resale
    resale_high = assessment.get('resale_high') or resale
    offer = ceiling((low+high)/2)
    if ceiling(high,resale_low)<=0: return None
    return {'offer':offer, 'low':ceiling(high,resale_low),'high':ceiling(low,resale_high),'gap':float(asking or 0)-offer,
            'lines':[('Property purchased',-offer),('Purchase closing costs',-offer*.01),
            ('Renovations',-(low+high)/2),('Cost of money',-offer*.1*10/12),
            ('Property tax',-offer*.011*10/12),('HOA carrying cost',-hoa*10),
            ('Sale closing costs',-resale*.065),('Expected resale',resale),('Estimated result',0)]}


def profit_at_asking(assessment, asking):
    keys = ('resale_midpoint', 'renovation_low', 'renovation_high', 'hoa_monthly')
    if any(isinstance(assessment.get(k), bool) or not isinstance(assessment.get(k), (int, float)) or not math.isfinite(assessment[k]) or assessment[k] < 0 for k in keys):
        return None
    if not isinstance(asking, (int, float)) or not math.isfinite(asking) or asking <= 0:
        return None
    if assessment['resale_midpoint'] <= 0 or assessment['renovation_high'] < assessment['renovation_low']:
        return None
    repairs = (assessment['renovation_low'] + assessment['renovation_high']) / 2
    return (assessment['resale_midpoint'] * .935 - asking * 1.1025
            - repairs - assessment['hoa_monthly'] * 10)


def register_active(app, dbs, signed_in, listing_view, demo_viewer):
    bp = Blueprint('active', __name__)

    def base_query():
        query = {'RAW_STATUS':{'$in':['ACTV','NEW','BOMK','PCH']},
                 'LATITUDE':{'$gte':36.8,'$lte':38.9}, 'LONGITUDE':{'$gte':-123.6,'$lte':-121.15},
                 'COUNTY_OR_PARISH':{'$in':BAY_COUNTIES + [c.upper() for c in BAY_COUNTIES]}}
        if demo_viewer(): query['LISTING_ID']={'$in':list(CANDIDATES)}
        return query

    @bp.get('/admin/active')
    @signed_in
    def search():
        source, result = dbs()
        query = base_query()
        # The demo only exposes listings whose assessment has been saved.
        # This expands naturally as the production insight pipeline fills in.
        insight_ids = set(result.active_renovation_insights.distinct('listing_id'))
        if demo_viewer() or request.args.get('candidates') == '1':
            insight_ids &= set(CANDIDATES)
        query['LISTING_ID'] = {'$in': sorted(key for key in insight_ids if isinstance(key, str))}
        city_query = dict(query)
        city = request.args.get('city','').strip().upper()
        if city: query['CITY']=city
        for key,op in [('min_price','$gte'),('max_price','$lte')]:
            value = request.args.get(key,'')
            if value:
                try:
                    value=float(value)
                    if not math.isfinite(value) or value<0: raise ValueError()
                    query.setdefault('LIST_PRICE',{})[op]=value
                except ValueError: abort(400, 'Enter a valid price.')
        beds=request.args.get('beds',type=int)
        if beds: query['BEDROOMS_TOTAL']={'$gte':beds}
        page=max(1,request.args.get('page',1,type=int)); per_page=60
        total=source.mls_listings.count_documents(query)
        docs=list(source.mls_listings.find(query,{'_id':0}).sort([('LIST_PRICE',1),('LISTING_ID',1)]).skip((page-1)*per_page).limit(per_page))
        assessments = {a['listing_id']: a for a in result.active_renovation_insights.find(
            {'listing_id': {'$in': [doc['LISTING_ID'] for doc in docs]}},
            {'_id':0,'listing_id':1,'resale_midpoint':1,'renovation_low':1,'renovation_high':1,'hoa_monthly':1})}
        rows=[]
        for doc in docs:
            view=listing_view(doc,demo=True)
            rows.append({'id':doc['LISTING_ID'],'address':view['address'],'city':view['city'],'price':doc.get('LIST_PRICE'), 'beds':view['beds'],'sqft':view['sqft'],'photo':next(iter(view['photos']),None),'lat':doc.get('LATITUDE'),'lng':doc.get('LONGITUDE'),'candidate':doc['LISTING_ID'] in CANDIDATES,'status':doc.get('RAW_STATUS')})
            rows[-1]['estimated_profit'] = profit_at_asking(assessments.get(doc['LISTING_ID'], {}), doc.get('LIST_PRICE'))
        cities=sorted(c for c in source.mls_listings.distinct('CITY',city_query) if c)
        return render_template('active_search.html', rows=rows,total=total,page=page,has_next=page*per_page<total,cities=cities,filters=request.args)

    @bp.get('/admin/active/<listing_id>')
    @signed_in
    def detail(listing_id):
        source, result=dbs()
        query=base_query(); query['LISTING_ID']=listing_id
        if demo_viewer() and listing_id not in CANDIDATES: abort(404)
        doc=source.mls_listings.find_one(query,{'_id':0})
        if not doc: abort(404)
        listing=listing_view(doc,demo=demo_viewer())
        labels={}
        for row in result.photo_classifications.find({'listing_id':listing_id},{'_id':0,'photo_url':1,'category':1}):
            labels[row['photo_url']]=row.get('category','other')
        for row in result.active_photo_classifications.find({'listing_id':listing_id},{'_id':0,'photo_url':1,'category':1}).sort('classified_at',1):
            labels[row['photo_url']]=row.get('category','other')
        groups=defaultdict(list)
        for photo in listing['photos']: groups[labels.get(photo,'unclassified')].append(photo)
        assessment=result.active_renovation_insights.find_one({'listing_id':listing_id},{'_id':0}) or {}
        comp_ids=[c.get('listing_id') for c in assessment.get('comps',[]) if c.get('listing_id')]
        comp_records={d['LISTING_ID']:d for d in source.mls_listings.find({'LISTING_ID':{'$in':comp_ids}}, {'_id':0,'LISTING_ID':1,'details.PRIMARY_PHOTO_URI':1,'photos':{'$slice':1}})}
        for comp in assessment.get('comps',[]):
            record=comp_records.get(comp.get('listing_id'),{})
            primary=(record.get('details') or {}).get('PRIMARY_PHOTO_URI')
            comp['photo']=primary if isinstance(primary,str) and primary.startswith('https://') else next(iter(listing_view(record,demo=True)['photos']),None)
        room_insights=defaultdict(list)
        for observation in assessment.get('observations',[]):
            area=str(observation.get('area','')).lower()
            key=next((key for term,key in [('kitchen','kitchen'),('bath','bathroom'),('bed','bedroom'),('living','living_room'),('dining','dining_room'),('laundry','laundry'),('garage','garage'),('exterior','exterior'),('landscap','exterior'),('outdoor','exterior')] if term in area),'other')
            room_insights[key].append(observation)
            groups.setdefault(key,[])
        hoa=(doc.get('details') or {}).get('HOA_FEE')
        recommendation = purchase_recommendation(assessment, doc.get('LIST_PRICE'))
        return render_template('active_detail.html',listing=listing,doc=doc,groups=dict(groups),assessment=assessment,comparison=CANDIDATES.get(listing_id),hoa=hoa,recommendation=recommendation,room_insights=dict(room_insights),renovation_items=renovation_bullets(assessment))

    @bp.get('/admin/comparable/<listing_id>')
    @signed_in
    def comparable(listing_id):
        source,result=dbs()
        if demo_viewer() and not result.active_renovation_insights.find_one({'listing_id':{'$in':list(CANDIDATES)},'comps.listing_id':listing_id},{'_id':1}):
            abort(404)
        doc=source.mls_listings.find_one({'LISTING_ID':listing_id,'RAW_STATUS':'SLD'},{'_id':0})
        if not doc: abort(404)
        listing=listing_view(doc,demo=demo_viewer())
        primary=(doc.get('details') or {}).get('PRIMARY_PHOTO_URI')
        listing['photos']=[url for url in listing['photos'] if '/HD_' in url or 'media.mlslmedia.com/property/' in url or ('.jpg' in url.lower() or '.jpeg' in url.lower())]
        if isinstance(primary,str) and primary.startswith('https://'):
            listing['photos']=list(dict.fromkeys([primary]+listing['photos']))
        return render_template('comparable_detail.html',listing=listing,doc=doc)

    app.register_blueprint(bp)
