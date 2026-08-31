# APE Synthetic Finance Act 2026

> Synthetic test document for RAG Builder regression testing only.
> This is NOT the real Bangladesh Finance Act and must not be used for legal or tax advice.

## Part 1 — Commencement

### Section 1 — Effective Date

This synthetic Finance Act takes effect on **1 July 2026**.

Unless a provision states otherwise, amendments in this Act apply only from that date.

---

## Part 2 — Amendment of Investment Rebate

### Section 5 — Amendment to Investment Rebate Rate

Section 21 of the **APE Synthetic Income Tax Act 2023** is amended.

From 1 July 2026, the investment rebate rate is changed from **15% to 10% of eligible investment**.

The revised rule is:

> A taxpayer is entitled to an investment rebate equal to **10% of eligible investment**.

The previous 15% rate remains relevant only for historical questions relating to periods before 1 July 2026.

### Section 6 — Example After Amendment

If eligible investment is **BDT 60,000**:

- Eligible investment: BDT 60,000
- Applicable rebate rate: 10%
- Investment rebate: **BDT 6,000**

The taxpayer-provided investment amount must be used directly.

The system must not substitute a different investment amount.

---

## Part 3 — Amendment of Tax-Free Threshold

### Section 10 — Revised Tax-Free Threshold

Section 10 of the APE Synthetic Income Tax Act 2023 is amended.

From 1 July 2026, the individual tax-free threshold is increased from **BDT 350,000 to BDT 400,000**.

For a current-period question after 1 July 2026, BDT 400,000 is the applicable threshold.

For a historical question before 1 July 2026, BDT 350,000 remains applicable.

---

## Part 4 — Source Tax Clarification

### Section 15 — Savings Certificate Source Tax

Section 31 of the APE Synthetic Income Tax Act 2023 is not changed.

The source-tax rate on approved savings-certificate profit remains **10%**.

This provision is included so that the regression suite can distinguish:

- a rule that was amended; and
- a rule that remained unchanged.

---

## Part 5 — Current Tax Calculation Example

### Section 20 — Individual Example for 2026 Rules

Assume:

- Gross income: BDT 900,000
- Allowable exemption: BDT 100,000
- Eligible investment: BDT 60,000

Using the 2026 rules:

1. Taxable income = 900,000 - 100,000 = **BDT 800,000**
2. Current tax-free threshold = BDT 400,000
3. Taxable amount above threshold = BDT 400,000
4. First BDT 100,000 × 5% = BDT 5,000
5. Next BDT 300,000 × 10% = BDT 30,000
6. Tax before rebate = **BDT 35,000**
7. Current investment rebate = 60,000 × 10% = **BDT 6,000**
8. Final tax = 35,000 - 6,000 = **BDT 29,000**

---

## Part 6 — Current Authority

### Section 30 — Relationship to the 2023 Act

For periods beginning on or after 1 July 2026:

- Section 5 of this Finance Act **modifies Section 21** of the APE Synthetic Income Tax Act 2023.
- Section 10 of this Finance Act **modifies Section 10** of the APE Synthetic Income Tax Act 2023.
- Section 15 confirms that Section 31 remains unchanged.

Where a current question conflicts with the older wording, the amended 2026 rule is authoritative.

Where a question explicitly asks about a period before 1 July 2026, the historical 2023 rule should be used.
