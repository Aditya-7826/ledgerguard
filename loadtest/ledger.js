// LedgerGuard ledger load test (k6). Run through `make loadtest`, which starts an isolated stack.
//
// Scenarios run back to back, each with VUS virtual users:
//   uniform      random sender -> random receiver from a pool of ACCOUNTS funded accounts
//   hot_account  80% of transfers go to ONE merchant (every one of them locks that merchant's row)
//   retry_storm  STORM logical transfers, each sent 2-3 times with the same Idempotency-Key; half the keys
//                fire their copies concurrently (http.batch), half as sequential client retries.
import http from "k6/http";
import { check } from "k6";
import exec from "k6/execution";
import { Counter } from "k6/metrics";

const BASE = __ENV.LEDGER_URL || "http://ledger-service:8080";
const API_KEY = __ENV.LEDGER_CLIENT_API_KEY;
const RUN = __ENV.RUN_ID || `lt${Date.now()}`;
const VUS = Number(__ENV.VUS || 50);
const DURATION_S = Number(__ENV.DURATION_S || 60);
const ACCOUNTS = Number(__ENV.ACCOUNTS || 200);
const STORM = Number(__ENV.STORM_REQUESTS || 10000);
const SUMMARY_OUT = __ENV.SUMMARY_OUT || "/work/results/k6-summary.json";

const stormKeys = new Counter("storm_keys");
const stormSends = new Counter("storm_sends");
const stormMismatch = new Counter("storm_mismatched_replays");
const stormNot201 = new Counter("storm_first_not_201");
const non201 = new Counter("transfer_non_201");

const scenarioTag = (s) => [`http_req_duration{scenario:${s}}`, `http_reqs{scenario:${s}}`, `http_req_failed{scenario:${s}}`];

export const options = {
  setupTimeout: "10m",
  summaryTrendStats: ["avg", "min", "p(50)", "p(95)", "p(99)", "max"],
  scenarios: {
    uniform: { executor: "constant-vus", exec: "uniform", vus: VUS, duration: `${DURATION_S}s`, gracefulStop: "10s" },
    hot_account: {
      executor: "constant-vus", exec: "hot", vus: VUS, duration: `${DURATION_S}s`, gracefulStop: "10s",
      startTime: `${DURATION_S + 15}s`,
    },
    retry_storm: {
      executor: "shared-iterations", exec: "storm", vus: VUS, iterations: STORM, maxDuration: "20m",
      startTime: `${2 * DURATION_S + 30}s`,
    },
  },
  // Trivial thresholds: they exist so k6 reports per-scenario sub-metrics in the summary.
  thresholds: Object.fromEntries(
    ["uniform", "hot_account", "retry_storm"].flatMap(scenarioTag).map((m) => [m, m.startsWith("http_req_duration") ? ["max>=0"] : m.startsWith("http_reqs") ? ["count>=0"] : ["rate>=0"]]),
  ),
};

const params = (key) => ({
  headers: { "Content-Type": "application/json", "X-API-Key": API_KEY, "Idempotency-Key": key },
  timeout: "60s",
});

function post(path, body, key) {
  return http.post(`${BASE}${path}`, JSON.stringify(body), params(key));
}

export function setup() {
  if (!API_KEY) throw new Error("LEDGER_CLIENT_API_KEY is not set");
  const create = (name) => ["POST", `${BASE}/v1/accounts`, JSON.stringify({ owner_name: name, currency: "INR" }),
    { headers: { "Content-Type": "application/json", "X-API-Key": API_KEY } }];
  const ids = [];
  for (let i = 0; i < ACCOUNTS + 1; i += 50) {
    const batch = [];
    for (let j = i; j < Math.min(i + 50, ACCOUNTS + 1); j++) batch.push(create(j === 0 ? "Hot Merchant" : `Load ${j}`));
    for (const r of http.batch(batch)) {
      if (r.status !== 201) throw new Error(`account create failed: ${r.status} ${r.body}`);
      ids.push(r.json("id"));
    }
  }
  const merchant = ids[0];
  const accounts = ids.slice(1);
  // ₹50 lakh each: far more than any sender can spend in the test, so no transfer is rejected for funds.
  for (let i = 0; i < accounts.length; i += 50) {
    const batch = accounts.slice(i, i + 50).map((id, j) => ["POST", `${BASE}/v1/deposits`,
      JSON.stringify({ account_id: id, amount_minor: 500_000_000, currency: "INR", memo: "load test funding" }),
      params(`${RUN}-fund-${i + j}`)]);
    for (const r of http.batch(batch)) if (r.status !== 201) throw new Error(`deposit failed: ${r.status} ${r.body}`);
  }
  return { merchant, accounts };
}

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
const amount = () => 100 + Math.floor(Math.random() * 99_900); // ₹1 – ₹1000, in paise

function pair(accounts) {
  const from = pick(accounts);
  let to = pick(accounts);
  while (to === from) to = pick(accounts);
  return [from, to];
}

function transfer(from, to, scenario) {
  const key = `${RUN}-${scenario}-${exec.vu.idInTest}-${exec.vu.iterationInScenario}`;
  const r = post("/v1/transfers", { from_account_id: from, to_account_id: to, amount_minor: amount(), currency: "INR" }, key);
  if (r.status !== 201) non201.add(1);
  check(r, { "transfer 201": (x) => x.status === 201 });
}

export function uniform(data) {
  const [from, to] = pair(data.accounts);
  transfer(from, to, "uni");
}

export function hot(data) {
  if (Math.random() < 0.8) transfer(pick(data.accounts), data.merchant, "hot");
  else {
    const [from, to] = pair(data.accounts);
    transfer(from, to, "hot");
  }
}

export function storm(data) {
  const i = exec.scenario.iterationInTest;
  const key = `${RUN}-storm-${i}`;
  const copies = 2 + (i % 2); // half the keys are sent twice, half three times
  const [from, to] = pair(data.accounts);
  const body = JSON.stringify({ from_account_id: from, to_account_id: to, amount_minor: 100 + (i % 9_900),
    currency: "INR", memo: "retry storm" });
  const url = `${BASE}/v1/transfers`;
  let res;
  if (Math.floor(i / 2) % 2 === 0) {
    res = http.batch(Array.from({ length: copies }, () => ["POST", url, body, params(key)]));
  } else {
    res = [];
    for (let c = 0; c < copies; c++) res.push(http.post(url, body, params(key)));
  }
  stormKeys.add(1);
  stormSends.add(copies);
  const same = res.every((r) => r.status === res[0].status && r.body === res[0].body);
  if (!same) stormMismatch.add(1);
  if (res[0].status !== 201) stormNot201.add(1);
  check(res, { "all copies got the identical response": () => same, "transfer 201": () => res[0].status === 201 });
}

export function handleSummary(data) {
  data.config = { run_id: RUN, vus: VUS, duration_s: DURATION_S, accounts: ACCOUNTS, storm_requests: STORM };
  return { [SUMMARY_OUT]: JSON.stringify(data, null, 1), stdout: "\nk6 summary written\n" };
}
