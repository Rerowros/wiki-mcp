import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import measurements as M
import lint
import wikilib as W

FIXTURE = Path(__file__).parent / "fixtures/measurement-cases.json"

class Measurements(unittest.TestCase):
    def test_version_selection_and_missing_cells(self):
        fixture=json.loads(FIXTURE.read_text())
        for case in fixture["cases"]:
            with self.subTest(case=case):
                result=M.resolve(fixture["records"],case["subject"],case["metric"],case.get("version"),True,fixture["today"])
                self.assertEqual([r["value"] for r in result["matched"]],case["values"])
                self.assertEqual(len(result["missing_current"]),case["missing"])
                self.assertEqual(result["historical_count"],case["historical"])
    def test_no_active_version_is_not_latest_numeric_version(self):
        r=M.resolve([dict(subject='m',metric='bench-v9',value=10,as_of='2026-09-01',source='a')])
        self.assertEqual(r['matched'],[])
        self.assertEqual(r['historical_count'],1)
    def test_review_due_is_explicit(self):
        fixture=json.loads(FIXTURE.read_text())
        r=M.resolve(fixture['records'],'model-a','bench-index',today=fixture['today'])
        self.assertEqual(r['matched'][0]['freshness'],'review_due')
    def test_same_day_revision_requires_two_observations(self):
        base=dict(subject='m',metric='bench-v1',as_of='2026-09-07',value=1)
        def report(a,b):
            pages={slug:W.Page(slug,'wiki/findings/'+slug+'.md',{'claims':[claim]},'','') for slug,claim in [('a',a),('b',b)]}
            r=lint.Report();lint.check_claims(pages,r);return r.errors
        self.assertTrue(report(base,dict(base,value=2)))
        self.assertTrue(report(base,dict(base,value=2,observed_at='2026-09-07T12:00:00Z')))
        self.assertFalse(report(dict(base,observed_at='2026-09-07T08:00:00Z'),dict(base,value=2,observed_at='2026-09-07T12:00:00Z')))
    def test_selector_requires_evidence_and_quoted_version(self):
        page=W.Page('bad','wiki/findings/bad.md',{'claims':[dict(subject='bench',metric='active-version',value=4.10,as_of='2026-09-07')]},'','')
        r=lint.Report();lint.check_claims({'bad':page},r)
        self.assertTrue(any('quoted' in m for _,m in r.errors))
        self.assertTrue(any('source_url' in m for _,m in r.errors))

    def test_malformed_claim_returns_validation_errors(self):
        page=W.Page('bad','wiki/findings/bad.md',{'claims':[dict(subject=7,metric='score',value=1,
            as_of='2026-09-07',source_url='https://[invalid',measurement_status=['measured'])]},'','')
        report=lint.Report();lint.check_claims({'bad':page},report)
        self.assertTrue(any('subject' in m for _,m in report.errors))
        self.assertTrue(any('source_url' in m for _,m in report.errors))

if __name__=='__main__':unittest.main()
