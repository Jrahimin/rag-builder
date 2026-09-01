# APE Synthetic Mixed-Language Tax Guidance 2025

> শুধুমাত্র RAG Builder regression testing-এর জন্য তৈরি কৃত্রিম নথি।
> এটি কোনো বাস্তব আইন, বিধি বা কর পরামর্শ নয়।

## উদ্দেশ্য

এই নথিটি ইচ্ছাকৃতভাবে বাংলা-প্রধান, তবে কিছু সম্পূর্ণ English line, identifier এবং technical instruction রাখা হয়েছে। উদ্দেশ্য হলো এমন বাস্তব নথির অনুকরণ করা যেখানে মূল বক্তব্য বাংলা হলেও form label, system instruction, reference code বা submission rule ইংরেজিতে থাকে।

এই guidance কেবল প্রক্রিয়াগত সহায়ক উৎস। এটি ২০২৩ Act, ২০২৪ Rules, ২০২৬ Finance Act বা পরবর্তী কোনো amendment-এর করহার, rebate rate, threshold বা eligibility পরিবর্তন করে না।

---

## ডিজিটাল প্রমাণ জমা

করদাতা যোগ্য বিনিয়োগের supporting evidence ডিজিটালভাবে জমা দিতে পারবেন। জমা দেওয়া তথ্যের সঙ্গে বিনিয়োগের অঙ্ক, লেনদেনের তারিখ এবং প্রমাণপত্রের পরিচয় মিলতে হবে।

For online submission, keep the certificate or statement together with the transaction date and declared investment amount.

সঞ্চয়পত্র, অবসরকালীন তহবিল বা জীবনবিমার প্রমাণে প্রতিষ্ঠানের নাম, করদাতার পরিচয় এবং সংশ্লিষ্ট লেনদেন বোঝা যায়—এমন তথ্য থাকা উচিত। কোনো ইংরেজি label থাকার কারণে বাংলা নথি অগ্রহণযোগ্য হবে না।

The uploaded evidence should preserve its original filename and submission timestamp.

---

## যাচাইকরণ রেফারেন্স

সহায়ক নথি যাচাইয়ের সময় system একটি স্বতন্ত্র verification reference সংরক্ষণ করবে। এই reference কেবল workflow tracking-এর জন্য; এটি কোনো substantive tax rule নয়।

Verification reference: `VR-2025-APE`

এই কৃত্রিম নথিতে `VR-2025-APE` একটি অনন্য fact, যাতে semantic chunking-এর পর mixed-language retrieval নির্ভরযোগ্যভাবে পরীক্ষা করা যায়।

এই reference rebate rate, tax-free threshold, source-tax rate বা eligible investment amount নির্ধারণ করে না।

---

## অতিরিক্ত নথি দেওয়ার সময়সীমা

প্রথম submission অসম্পূর্ণ হলে করদাতাকে প্রয়োজনীয় অতিরিক্ত supporting document জমা দেওয়ার সুযোগ দেওয়া যেতে পারে।

The additional-document review window is **14 calendar days**.

এই ১৪ দিনের সময়সীমা শুধু প্রক্রিয়াগত। এর কারণে কোনো করহার, রিবেটের হার, করমুক্ত সীমা বা উৎসে করের হার পরিবর্তিত হবে না।

সময় গণনার ক্ষেত্রে calendar day ব্যবহার করা হবে; business day ধরে নতুন কোনো সময়সীমা অনুমান করা যাবে না।

---

## ভাষা ও নথির ধরন

সহায়ক প্রমাণ বাংলা, English অথবা দুই ভাষার সমন্বয়ে হতে পারে। বাস্তব নথিতে ব্যাংকের নাম, policy number, certificate label, account reference বা transaction description ইংরেজিতে থাকা স্বাভাবিক।

A document must not be rejected only because some labels or institution-provided fields are in English.

একইভাবে ইংরেজি নথির মধ্যে বাংলা নাম, ঠিকানা বা মন্তব্য থাকলেও সেটিকে অসঙ্গত ধরা যাবে না। retrieval-এর লক্ষ্য হবে অর্থ ও প্রাসঙ্গিক evidence খোঁজা, শুধু একটি ভাষার exact শব্দ খোঁজা নয়।

---

## অন্যান্য কৃত্রিম উৎসের সঙ্গে সম্পর্ক

এই guidance একটি supplementary procedural source। এটি কোনো `MODIFIES` সম্পর্ক তৈরি করে না।

এটি ২০২৩ সালের substantive বিধানকে বাতিল করে না, ২০২৪ Rules-এর প্রমাণসংক্রান্ত নিয়মকে প্রতিস্থাপন করে না এবং ২০২৬ বা ২০২৭ সালের amendment-এর ওপর অগ্রাধিকার পায় না।

If the question asks for the current rebate rate, this document is not the authority for that answer.

কিন্তু যদি প্রশ্ন হয় verification reference কী, অথবা অতিরিক্ত document দেওয়ার review window কতদিন, তাহলে এই নথিই প্রাসঙ্গিক উৎস।

---

## Regression facts

এই fixture-এর অনন্য fact:

- verification reference: `VR-2025-APE`
- অতিরিক্ত নথি দেওয়ার সময়: **14 calendar days**
- বাংলা নথিতে English label ও technical field থাকতে পারে
- এটি procedural guidance; substantive tax authority পরিবর্তন করে না
