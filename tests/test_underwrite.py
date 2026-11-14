from flip_finder.underwrite import UnderwritingAssumptions, underwrite


def test_lower_arv_and_longer_hold_reduce_profit():
    a = UnderwritingAssumptions(annual_interest_rate=.12, loan_to_cost=.8)
    base = underwrite(1_200_000, 850_000, 100_000, 6, a)
    assert underwrite(1_080_000, 850_000, 100_000, 6, a).profit_pre_tax < base.profit_pre_tax
    assert underwrite(1_200_000, 850_000, 100_000, 9, a).profit_pre_tax < base.profit_pre_tax
