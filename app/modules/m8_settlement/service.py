from .rules import settlement
def execute(ctx):
    result = settlement(ctx['intervals'], ctx['calendar'], ctx['reconciliation'], ctx['conflicts'])
    ctx['settlement'] = result
    ctx['stages'].append({'title': '结算床日', 'rows': result['rows'], 'metrics': {'结算床日': result['totals']['bed_days'], '占床 min': str(result['totals']['minutes']), '待复核': len(result['pending'])}})
    return ctx
