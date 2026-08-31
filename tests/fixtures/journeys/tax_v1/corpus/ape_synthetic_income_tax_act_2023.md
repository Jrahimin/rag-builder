# APE Synthetic Income Tax Act 2023

> Synthetic test document for RAG Builder regression testing only.
> This is NOT the real Bangladesh Income Tax Act and must not be used for legal or tax advice.

## Chapter 1 — General Rules

### Section 1 — Scope

This Act defines the synthetic income-tax rules used by the APE regression suite.

The rules apply to the fictional tax year beginning on 1 July 2023.

### Section 2 — Taxable Income

Taxable income means total assessable income after deducting any exemption expressly allowed by this Act.

For the regression suite, taxable income is calculated as:

`taxable_income = gross_income - allowable_exemption`

No other deductions may be assumed unless they are stated in this document.

---

## Chapter 2 — Individual Tax Rates

### Section 10 — Tax-Free Threshold

An individual taxpayer receives a tax-free threshold of **BDT 350,000**.

Income up to this threshold is taxed at 0%.

### Section 11 — Simplified Tax Slabs

For this synthetic Act, income above the tax-free threshold is taxed as follows:

1. First BDT 100,000 above the threshold: **5%**
2. Next BDT 300,000: **10%**
3. Remaining taxable income: **15%**

These simplified slabs exist only for automated regression calculations.

---

## Chapter 3 — Investment Rebate

### Section 20 — Eligible Investment

The following are eligible investments:

- approved savings certificates;
- approved retirement contributions;
- approved life-insurance premiums.

Only the amount expressly provided by the taxpayer may be treated as eligible investment.

The system must not estimate or replace the provided investment amount.

### Section 21 — Investment Rebate Rate

A taxpayer is entitled to an investment rebate equal to **15% of eligible investment**.

Example:

- Eligible investment: BDT 60,000
- Rebate rate: 15%
- Investment rebate: BDT 9,000

### Section 22 — Rebate Limit

The investment rebate cannot exceed the taxpayer's tax liability before rebate.

If the calculated rebate is greater than the tax liability before rebate, the allowed rebate equals the tax liability before rebate.

---

## Chapter 4 — Tax Deducted at Source

### Section 30 — Source Tax Categories

Tax may be deducted at source from the following synthetic categories:

1. interest from savings certificates;
2. contractor payments;
3. property transfer payments;
4. professional service payments.

### Section 31 — Savings Certificate Source Tax

Tax deducted at source from approved savings-certificate profit is **10%**.

This source tax is separate from the investment rebate under Section 21.

---

## Chapter 5 — Calculation Example

### Section 40 — Individual Example

Assume:

- Gross income: BDT 900,000
- Allowable exemption: BDT 100,000
- Eligible investment: BDT 60,000

Then:

1. Taxable income = 900,000 - 100,000 = **BDT 800,000**
2. Tax-free threshold = BDT 350,000
3. Taxable amount above threshold = BDT 450,000
4. First BDT 100,000 × 5% = BDT 5,000
5. Next BDT 300,000 × 10% = BDT 30,000
6. Remaining BDT 50,000 × 15% = BDT 7,500
7. Tax before rebate = **BDT 42,500**
8. Investment rebate under Section 21 = 60,000 × 15% = **BDT 9,000**
9. Final tax = 42,500 - 9,000 = **BDT 33,500**

---

## Chapter 6 — Authority and Amendment

### Section 50 — Amendment Rule

If a later Finance Act expressly changes a rate or rule in this Act, the later Finance Act controls from its stated effective date.

A later provision should modify only the provision that it expressly identifies.

Historical rules remain valid when the question is explicitly about a period before the later amendment took effect.
