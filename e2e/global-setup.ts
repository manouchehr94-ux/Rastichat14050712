import { execFileSync } from 'child_process';
import { writeFileSync } from 'fs';
import path from 'path';

// Pulls the seeded fixture IDs (project public_key, product id) straight out of
// the database via a one-off Django shell command, instead of hardcoding
// environment-specific UUIDs into a committed test file. Requires the same
// backend venv/env vars used to run `python manage.py test`.
export default function globalSetup() {
  const backendDir = path.resolve(__dirname, '..', 'backend');

  const script = `
import json
from projects.models import Project
from catalog.models import Product
from teams.models import Team
from queues.models import Queue
project = Project.objects.get(name='Sample Website')
product = Product.objects.filter(workspace=project.workspace).first()
team = Team.objects.filter(workspace=project.workspace, name='فروش').first()
queue = Queue.objects.filter(workspace=project.workspace, name='صف فروش').first()
print('FIXTURE_JSON=' + json.dumps({
    'projectKey': str(project.public_key),
    'productId': str(product.id) if product else None,
    'teamId': str(team.id) if team else None,
    'queueId': str(queue.id) if queue else None,
}))
`;

  const pythonBin = process.env.E2E_PYTHON || 'python';
  const output = execFileSync(pythonBin, ['manage.py', 'shell', '-c', script], {
    cwd: backendDir,
    env: process.env,
    encoding: 'utf-8',
  });

  const match = output.split('\n').find((line) => line.startsWith('FIXTURE_JSON='));
  if (!match) {
    throw new Error(`Could not extract fixture JSON from Django shell output:\n${output}`);
  }
  const fixture = JSON.parse(match.replace('FIXTURE_JSON=', ''));
  writeFileSync(path.resolve(__dirname, '.fixture.json'), JSON.stringify(fixture, null, 2));
}
