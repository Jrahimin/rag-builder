"""Approved frozen question identities; aliases preserve every prior question."""

REMEDIATION_ALIASES = {
    "latest-01": "Q1",
    "latest-02": "Q2",
    "latest-03": "Q3",
    "latest-04": "Q4",
    "latest-05": "Q5",
    "latest-06": "Q6",
    "latest-07": "Q7",
    "earlier-7": "earlier_agm",
    "earlier-8": "earlier_threshold",
    "earlier-9": "yearless_threshold",
    "earlier-10": "dncc",
    "earlier-11": "historical_threshold",
    "earlier-12": "rent",
    "earlier-13": "partnership",
    "earlier-14": "salary_retest",
    "earlier-15": "unsupported_retest",
}

REMEDIATION_QUESTIONS = {
    "Q1": (
        "Using only the active indexed corpus, for AY 2026â€“27, what is the general "
        "tax-free income threshold for a resident ordinary individual? State the "
        "governing statutory provision, any applicable Finance Act amendment, and the"
        " effective period. If applicability of a 2026 change cannot be established "
        "from the evidence, identify the missing proof instead of assuming."
    ),
    "Q2": (
        "Using only the indexed corpus, what was the general tax-free income "
        "threshold for a resident ordinary individual in AY 2025â€“26? Cite the "
        "governing source and period. Keep that historical rule separate from AY "
        "2026â€“27, and identify any unresolved amendment or effective-date link "
        "rather than applying the newest publication automatically."
    ),
    "Q3": (
        "For AY 2026â€“27, explain the income-tax rebate formula and maximum limit "
        "for eligible investment for an ordinary resident individual. This is a rule "
        "lookup, not a personal calculation, so no personal investment amount is "
        "needed. Cite the governing provision and applicable effective period; if the"
        " corpus cannot establish which rule governs this AY, identify that gap."
    ),
    "Q4": (
        "Synthetic scenario only: assume AY 2026â€“27, a resident ordinary individual"
        " under 65 with no special category, gross salary BDT 1,200,000, and eligible"
        " investment BDT 100,000. No other income, TDS, or asset facts are supplied. "
        "Give a provisional income-tax estimate before TDS and wealth-based "
        "surcharge. Show salary-to-taxable income, the complete applicable tax bands,"
        " investment rebate and cap, minimum-tax comparison, and governing citations."
        " Do not request optional refinements before giving the supported estimate; "
        "if a legal rule or effective-period link is missing, provide the supported "
        "partial calculation and name the precise gap."
    ),
    "Q5": (
        "Using only the active indexed corpus, what does Companies Act 1994 section "
        "81 say about the deadline for the first annual general meeting and the "
        "interval between later AGMs? Identify the statutory source and quote or "
        "accurately paraphrase the timing. If applicability as a current rule cannot "
        "be verified because effective dates are missing, make that limitation "
        "explicit rather than guessing."
    ),
    "Q6": (
        "Using only this project corpus, what is the current RJSC fee for "
        "incorporating a private limited company? If no current fee schedule is in "
        "the indexed project sources, say it is not established here and do not use "
        "web search or invent a figure."
    ),
    "Q7": (
        "Using only the indexed project corpus, summarize what the Budget Speech "
        "2026â€“27 document proposes about the personal income-tax threshold. Label "
        "this strictly as a budget proposal, not enacted or current law; do not infer"
        " legal effect. Cite the document and state if the relevant passage was not "
        "retrieved."
    ),
    "earlier_agm": (
        "Under the Companies Act 1994 in this corpus, when should a company hold its "
        "first annual general meeting, and what is the maximum interval between "
        "subsequent AGMs? Cite the section."
    ),
    "earlier_threshold": (
        "For an ordinary resident individual, what is the tax-free income limit for "
        "assessment year 2026â€“27? Cite the applicable rate schedule."
    ),
    "yearless_threshold": (
        "As of now, what is the tax-free income threshold for an ordinary resident "
        "individual in Bangladesh? State the applicable assessment year and cite the "
        "operative schedule."
    ),
    "dncc": (
        "According to the active DNCC trade licence procedure in this corpus, what "
        "documents are required to apply for a new trade licence? Cite the source."
    ),
    "historical_threshold": (
        "For a resident individual, what was the tax-free income limit for assessment"
        " year 2025â€“26, and which rate schedule applied?"
    ),
    "rent": (
        "Under the 2026 Withholding Tax Rules and the active amendment in this "
        "corpus, what tax must a specified person deduct when paying rent for office "
        "premises? Give the applicable rate or table and cite the rule."
    ),
    "partnership": (
        "According to the Partnership Act source in this project, is registration of "
        "a firm compulsory, and what consequence of non-registration is specified? "
        "Cite the provision."
    ),
    "salary_retest": (
        "Assume an ordinary resident individual has BDT 2,400,000 gross employment "
        "income for assessment year 2026â€“27 and BDT 100,000 eligible investment. "
        "Using the official sources in this project, estimate income tax before TDS "
        "and wealth surcharge, show the taxable-income calculation and applicable "
        "rate bands, and cite the schedule."
    ),
    "unsupported_retest": (
        "For assessment year 2026â€“27, what Bangladesh income-tax rate applies "
        "specifically to income from cryptocurrency mining? Cite the governing "
        "official source, or say if the project corpus cannot establish one."
    ),
}
