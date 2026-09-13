"""Shared inventory and listed-shop ownership capacity."""
def available_slots(snapshot):
    capacity=snapshot.get('capacity')
    if type(capacity) is not int or not 0<=capacity<=40:
        raise ValueError('Invalid combined merchant capacity')
    owned=snapshot['inventory']+snapshot.get('booth',[])
    uids=[i['uid'] for i in owned]
    if len(set(uids))!=len(uids):raise ValueError('Ambiguous inventory and shop ownership')
    return max(0,capacity-len(uids))
