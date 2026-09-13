from pathlib import Path
p=Path('backend/app/modules/conversations/services/chat_service.py')
s=p.read_text(encoding='utf-8')
needle='''            if bangla:
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু নিয়মগুলোর প্রযোজ্য সময়কাল, শর্ত বা "'''
replacement='''            if not _requires_calculation_coverage(question, prepared.chunks):
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু বিধানগুলোর প্রযোজ্য সময়কাল, শর্ত বা "
                    "সংশোধনের প্রভাব নিশ্চিত করা যায়নি। নির্ভরযোগ্য উত্তর দিতে প্রযোজ্য "
                    "বিধান ও সংশোধনের নির্দিষ্ট প্রমাণ দরকার।"
                    if bangla
                    else "Relevant sources were found, but their applicable period, conditions or "
                    "amendment effect could not be established. The applicable provisions and "
                    "amendment evidence are needed to answer this question reliably."
                )
            if bangla:
                return (
                    "প্রাসঙ্গিক সূত্র পাওয়া গেছে, কিন্তু নিয়মগুলোর প্রযোজ্য সময়কাল, শর্ত বা "'''
assert s.count(needle)==1
p.write_text(s.replace(needle,replacement),encoding='utf-8')
p=Path('tests/unit/modules/conversations/test_chat_service.py')
s=p.read_text(encoding='utf-8')
s+='''

@pytest.mark.parametrize(
    ("question", "calculation"),
    [
        ("What annual compliance obligations does a non-operating private company have?", False),
        ("ব্যবসা শুরু না করা প্রাইভেট কোম্পানির বার্ষিক করণীয় কী?", False),
        ("Calculate my income tax.", True),
        ("আমার আয়কর হিসাব করুন।", True),
    ],
)
def test_authority_refusal_describes_compliance_without_calling_it_calculation(
    question: str, calculation: bool
) -> None:
    from app.modules.conversations.schemas.message import InsufficientEvidenceReason

    service = MagicMock()
    service._evidence_approach = "authoritative"
    prepared = MagicMock()
    prepared.web_search_diagnostics = {}
    prepared.evidence.reason = InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
    chunk = MagicMock()
    chunk.metadata = {"source_role": "primary", "source_lifecycle_status": "active"}
    prepared.chunks = [chunk]
    content = ChatService._insufficient_content(service, prepared, question)
    mentions_calculation = "final calculation" in content or "চূড়ান্ত হিসাব" in content
    assert mentions_calculation is calculation
    assert "amendment" in content or "সংশোধন" in content
'''
p.write_text(s,encoding='utf-8')
