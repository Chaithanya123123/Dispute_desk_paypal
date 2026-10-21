'use client';
import { useEffect, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-quartz.css';

const API = process.env.NEXT_PUBLIC_API || 'http://localhost:8000';

const CHECKLIST: Record<string, string[]> = {
  ITEM_NOT_RECEIVED: ['Tracking number', 'Delivery confirmation', 'Ship date', 'Buyer communication'],
  NOT_AS_DESCRIBED: ['Product photos', 'Listing description', 'Return policy', 'Return offer'],
  UNAUTHORIZED: ['AVS/CVV match', 'IP/device evidence', 'Address match', 'Delivery proof'],
  OTHER: ['Dispute classification', 'Specialist review'],
};

export default function Page() {
  const [cases, setCases] = useState<any[]>([]);
  const [audit, setAudit] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [policy, setPolicy] = useState<any>(null);
  const [err, setErr] = useState('');

  const load = async () => {
    try {
      const [c, a] = await Promise.all([
        fetch(`${API}/api/cases`).then(r => r.json()),
        fetch(`${API}/api/audit`).then(r => r.json()),
      ]);
      setCases(c); setAudit(a); setErr('');
      setSelected((prev: any) =>
        prev ? (c.find((x: any) => x.case_id === prev.case_id) || (c[0] ?? null)) : (c[0] ?? null));
    } catch {
      setErr(`Backend unreachable at ${API} — is uvicorn running?`);
    }
  };
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, []);
  useEffect(() => {
    fetch(`${API}/api/policy`).then(r => r.json()).then(setPolicy).catch(() => {});
  }, []);

  const simulate = async (amount: number, reason: string, tracking = 'Delivered Oct 10, signed') => {
    await fetch(`${API}/api/simulate/dispute`, { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ amount, reason, tracking_status: tracking, buyer_email: 'buyer@example.com' }) });
    load();
  };
  const simulateScenario = async (scenario: string) => {
    await fetch(`${API}/api/simulate/dispute`, { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ scenario }) });
    load();
  };
  const act = async (id: string, kind: string) => {
    await fetch(`${API}/api/cases/${id}/${kind}`, { method: 'POST' }); load();
  };
  const saveDraft = async (id: string) => {
    const el = document.getElementById(`draft-${id}`) as HTMLTextAreaElement | null;
    if (!el) return;
    await fetch(`${API}/api/cases/${id}/draft`, { method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ draft_response: el.value }) });
    load();
  };
  const counteroffer = async (id: string) => {
    const el = document.getElementById(`offer-${id}`) as HTMLInputElement | null;
    const amt = parseFloat(el?.value || '0');
    if (!(amt > 0)) return;
    await fetch(`${API}/api/cases/${id}/counteroffer`, { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ amount: amt }) });
    load();
  };
  const savePolicy = async () => {
    await fetch(`${API}/api/policy`, { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(policy) });
    fetch(`${API}/api/policy`).then(r => r.json()).then(setPolicy).catch(() => {});
    load();
  };

  const cols = [
    { field: 'case_id', headerName: 'Case', flex: 1 },
    { field: 'reason', flex: 1 },
    { field: 'amount', valueFormatter: (p: any) => `${p.data?.currency && p.data.currency !== 'USD' ? p.data.currency + ' ' : ''}$${p.value}` },
    { field: 'urgency', cellClassRules: {
        'u-passed': (p: any) => p.value === 'PASSED',
        'u-critical': (p: any) => p.value === 'CRITICAL',
        'u-warning': (p: any) => p.value === 'WARNING',
      },
      valueFormatter: (p: any) => {
        const v = p.value || 'NORMAL';
        return v === 'PASSED' ? 'PASSED!' : v === 'CRITICAL' ? '<24h' : v === 'WARNING' ? '<72h' : '';
      }, headerName: 'Deadline', width: 90 },
    { field: 'status', flex: 1 },
    { field: 'policy_decision', headerName: 'Agent decision', flex: 1 },
    { field: 'confidence', headerName: 'Conf %', width: 80, valueFormatter: (p: any) => `${Math.round((p.value||0)*100)}%` },
  ];
  const auditCols = [
    { field: 'created_at', headerName: 'Time', flex: 1 },
    { field: 'case_id', headerName: 'Case', flex: 1 },
    { field: 'action', flex: 1 },
    { field: 'reason', flex: 2 },
    { field: 'actor', flex: 1 },
  ];

  const checklist = (c: any) => (CHECKLIST[c.reason] || CHECKLIST.OTHER).map((item: string) => {
    const miss = (c.missing_items || []).join(' ').toLowerCase();
    const words = item.toLowerCase().split(' ');
    const isMissing = words.some(w => w.length > 3 && miss.includes(w));
    return { item, ok: !isMissing };
  });
  const urgencyColor = (u: string) =>
    u === 'PASSED' ? '#ff7b72' : u === 'CRITICAL' ? '#ffa657' : u === 'WARNING' ? '#e3b341' : 'inherit';

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <style>{`
        .u-passed { color: #ff7b72; font-weight: 700; }
        .u-critical { color: #ffa657; font-weight: 700; }
        .u-warning { color: #e3b341; }
        .ag-theme-quartz .u-passed { background: rgba(255,123,114,.15); }
        .ag-theme-quartz .u-critical { background: rgba(255,166,87,.12); }
      `}</style>
      <h1>DisputeDesk — PayPal Dispute Evidence Agent</h1>
      <p style={{ opacity: .7 }}>The AI prepares the case, the seller&apos;s rules decide the money, and every step is logged.</p>
      {err && <p style={{ color: '#ff7b72', background: '#2d1b1b', padding: 8, borderRadius: 8 }}>{err}</p>}
      <div style={{ display: 'flex', gap: 8, margin: '16px 0', flexWrap: 'wrap', alignItems: 'center' }}>
        <button onClick={() => simulate(29.99, 'ITEM_NOT_RECEIVED')}>Simulate $29 dispute (auto-resolve)</button>
        <button onClick={() => simulate(25.0, 'UNAUTHORIZED')}>Simulate $25 unauthorized (auto-refund)</button>
        <button onClick={() => simulate(349.0, 'ITEM_NOT_RECEIVED')}>Simulate $349 dispute (needs approval)</button>
        <select defaultValue="" onChange={e => { if (e.target.value) simulateScenario(e.target.value); e.target.value = ''; }}>
          <option value="">Edge scenarios ▾</option>
          <option value="injection">Prompt-injection buyer</option>
          <option value="deadline_soon">Due in 18h (queue jump)</option>
          <option value="deadline_passed">Deadline passed</option>
          <option value="repeat_buyer">Repeat disputant</option>
          <option value="non_usd">Non-USD (EUR) claim</option>
          <option value="address_mismatch">Address mismatch</option>
          <option value="already_refunded">Already refunded</option>
          <option value="partial_dispute">Partial dispute ($30 of $180)</option>
        </select>
        <button onClick={load}>Refresh</button>
      </div>
      {policy && (
        <div style={{ background: '#151a26', padding: 12, borderRadius: 12, marginBottom: 16, display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <b>Merchant policy (seller sets the limits):</b>
          {(['auto_resolve_under', 'auto_resolve_not_as_described_under', 'auto_refund_unauthorized_under', 'min_confidence'] as const).map(k => (
            <label key={k} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              {k}: <input type="number" step="0.05" value={policy[k]}
                onChange={e => setPolicy({ ...policy, [k]: parseFloat(e.target.value) })}
                style={{ width: 72 }} />
            </label>
          ))}
          <button onClick={savePolicy}>Save policy</button>
        </div>
      )}
      <h3>Case queue (AG Grid) — urgent deadlines float to the top</h3>
      <div className="ag-theme-quartz" style={{ height: 280 }}>
        <AgGridReact rowData={cases} columnDefs={cols as any} onRowClicked={(e) => setSelected(e.data)} />
      </div>
      {selected && (
        <div style={{ background: '#151a26', padding: 16, borderRadius: 12, marginTop: 16 }}>
          <h3>
            {selected.case_id} — {selected.currency && selected.currency !== 'USD' ? `${selected.currency} ` : ''}{selected.amount_known === false ? '$(unknown)' : `$${selected.amount}`} — {selected.status}
            {' '}<span style={{ color: urgencyColor(selected.urgency), fontSize: 16 }}>
              {selected.urgency === 'PASSED' ? '⚠ deadline passed' : selected.urgency !== 'NORMAL' ? `⏳ ${selected.urgency}` : ''}
            </span>
          </h3>
          <p style={{ fontSize: 13, opacity: .75 }}>Respond by: {selected.respond_by || 'n/a'}</p>
          {selected.risk_flags?.length > 0 && (
            <p style={{ color: '#ffa657' }}><b>🚩 Risk flags (code, not the model):</b> {selected.risk_flags.join(', ')} — auto-action vetoed</p>
          )}
          <p>
            <b>Agent:</b>{' '}
            {selected.agent_provider === 'template'
              ? <span style={{ color: '#e3b341' }}>template fallback (no LLM key configured — deterministic)</span>
              : <span style={{ color: '#3fb950' }}>{selected.agent_provider || 'unknown'}</span>}
            {' '}— recommendation: {selected.recommended_action || 'n/a'}, confidence {Math.round((selected.confidence||0)*100)}%
          </p>
          {selected.agent_raw_output ? (
            <details style={{ marginBottom: 8 }}>
              <summary style={{ cursor: 'pointer', fontSize: 13 }}>Raw model output (JSON)</summary>
              <pre style={{ fontSize: 11, background: '#0d1117', padding: 8, borderRadius: 8, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto' }}>{selected.agent_raw_output}</pre>
            </details>
          ) : (
            <p style={{ fontSize: 13, opacity: .6 }}>No raw model output — template fallback used.</p>
          )}
          {selected.agent_error && <p style={{ color: '#ff7b72' }}><b>Validation failed:</b> {selected.agent_error} — forced human review</p>}
          <p><b>Decision:</b> {selected.policy_decision} — {selected.policy_reason}</p>
          <p><b>Evidence:</b><br /><span style={{ whiteSpace: 'pre-wrap' }}>{selected.evidence_summary}</span></p>
          <p style={{ fontSize: 13 }}>
            <b>PayPal evidence checklist:</b>{' '}
            {checklist(selected).map((c: any, i: number) => (
              <span key={i} style={{ color: c.ok ? '#3fb950' : '#ff7b72', marginRight: 10 }}>
                {c.ok ? '✓' : '✗'} {c.item}
              </span>
            ))}
          </p>
          <p><b>Buyer messages (untrusted):</b></p>
          {(selected.customer_messages || []).map((m: any, i: number) => (
            <p key={i} style={{ fontSize: 13, margin: '4px 0', opacity: m.from === 'buyer' ? 1 : .7 }}>
              <b>{m.from}:</b> {m.text}
            </p>
          ))}
          <p><b>Draft response:</b></p>
          <textarea id={`draft-${selected.case_id}`} key={selected.case_id + selected.status}
            defaultValue={selected.drafted_response} rows={7} style={{ width: '100%', background: '#0d1117', color: '#e6edf3', border: '1px solid #30363d', borderRadius: 8, padding: 8 }} />
          <p><b>Missing:</b> {(selected.missing_items || []).join(', ') || 'none'}</p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {selected.status === 'WAITING_APPROVAL' ? (
              <>
                <button onClick={() => saveDraft(selected.case_id)}>Save edited draft</button>
                <button onClick={() => act(selected.case_id, 'approve')}>Approve & Submit to PayPal</button>
                <span style={{ opacity: .7 }}>or offer:</span>
                <input id={`offer-${selected.case_id}`} type="number" step="1" max={selected.amount}
                  placeholder={`max $${selected.amount}`} style={{ width: 100 }} />
                <button onClick={() => counteroffer(selected.case_id)}>Counteroffer refund</button>
                <button onClick={() => act(selected.case_id, 'reject')}>Reject</button>
              </>
            ) : (
              <span style={{ opacity: .6 }}>No action needed — {selected.status}</span>
            )}
          </div>
        </div>
      )}
      <h3 style={{ marginTop: 24 }}>Audit log (AG Grid) — Agentic Commerce proof</h3>
      <div className="ag-theme-quartz" style={{ height: 240 }}>
        <AgGridReact rowData={audit} columnDefs={auditCols as any} />
      </div>
    </div>
  );
}
