// Provision the host's organisations as RastiChat tenants (idempotent: safe to re-run), then write tenants.json.
//   node provision.mjs   (same env as server.mjs)
import fs from 'node:fs';
import { RastiChatClient } from './lib/rastichat.mjs';

const env = (k, d) => process.env[k] ?? d;
const client = new RastiChatClient({
  baseUrl: env('RASTICHAT_URL', 'http://localhost:8080'), slug: env('INTEGRATION_SLUG', 'acme-learn'),
  kid: env('KEY_ID'), privateKeyPem: fs.readFileSync(env('PRIVATE_KEY_FILE', './host.private.pem'), 'utf8'),
});
const domains = env('VERIFIED_DOMAINS', 'localhost:4000').split(',');

// Three organisations demonstrate the three pre-chat modes and the launcher variants — configuration only.
const orgs = {
  'org-a': { name: 'Acme Academy', widget: { launcher: { mode: 'icon' }, pre_chat: { enabled: false } } },                          // icon only, no questions
  'org-b': { name: 'Beta School', widget: { launcher: { mode: 'icon_text', label: 'Ask us' },                                     // one question
    pre_chat: { enabled: true, fields: [{ key: 'help', type: 'text', label: 'What can we help you with?', required: true }] } } },
  'org-c': { name: 'Gamma College', widget: { launcher: { mode: 'icon_text', label: 'Help', position: 'bottom-left' },            // structured form
    pre_chat: { enabled: true, title: 'Before we start', fields: [
      { key: 'topic', type: 'select', label: 'Topic', required: true, order: 1, choices: [{ value: 'billing', label: 'Billing' }, { value: 'course', label: 'A course' }] },
      { key: 'ref', type: 'text', label: 'Reference number', order: 2, max_length: 20 },
      { key: 'agree', type: 'consent', label: 'I accept the terms', required: true, order: 3 }] } } },
};

const out = {};
for (const [id, org] of Object.entries(orgs)) {
  const t = await client.ensureTenant(id, { display_name: org.name, verified_domains: domains, defaults: { widget: org.widget } });
  // the host decides who staffs which tenant (here: bob -> org-a, erin -> org-b); RastiChat only sees generic roles
  if (id === 'org-a') await client.setStaffMember(id, 'bob', 'admin', 'Bob (teacher)');
  if (id === 'org-b') await client.setStaffMember(id, 'erin', 'operator', 'Erin (teacher)');
  out[id] = { name: org.name, project_public_key: t.project_public_key, workspace_id: t.workspace_id };
  console.log(`${id}: ${t.created ? 'created' : 'exists'} workspace=${t.workspace_id} project=${t.project_public_key}`);
}
fs.writeFileSync(env('TENANTS_FILE', './tenants.json'), JSON.stringify(out, null, 2));
