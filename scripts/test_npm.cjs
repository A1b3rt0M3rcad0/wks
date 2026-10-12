'use strict';
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const metadata = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'wks-npm-test-'));
function run(args) {
 const result = spawnSync(process.execPath, [path.join(root, 'bin/wksctl.cjs'), ...args], { encoding: 'utf8', cwd: temporary });
 assert.ifError(result.error);
 return result;
}
try {
 let result = run(['version']);
 assert.equal(result.status, 0, result.stderr);
 assert.deepEqual(JSON.parse(result.stdout), {version: metadata.version, commit: metadata.wksCommit});
 result = run(['help', '--json']);
 assert.equal(result.status, 0, result.stderr);
 assert.ok(JSON.parse(result.stdout).local_commands.includes('skill install --directory PATH'));
 fs.writeFileSync(path.join(temporary, 'AGENTS.md'), 'existing instructions');
 result = run(['skill', 'install']);
 assert.equal(result.status, 0, result.stderr);
 assert.equal(JSON.parse(result.stdout).changed, true);
 const skill = path.join(temporary, '.agents/skills/wks/SKILL.md');
 assert.equal(fs.readFileSync(skill, 'utf8'), fs.readFileSync(path.join(root, 'SKILL.md'), 'utf8'));
 assert.equal(fs.readFileSync(path.join(temporary, 'AGENTS.md'), 'utf8'), 'existing instructions');
 assert.equal(fs.existsSync(path.join(temporary, '.wks')), false);
 result = run(['skill', 'install']);
 assert.equal(result.status, 0, result.stderr);
 assert.equal(JSON.parse(result.stdout).changed, false);
 fs.writeFileSync(skill, 'customized skill');
 result = run(['skill', 'install']);
 assert.equal(result.status, 2, result.stderr);
 assert.equal(fs.readFileSync(skill, 'utf8'), 'customized skill');
 result = run(['invalid-command']);
 assert.equal(result.status, 2);
 console.log(`npm client ${metadata.version}: native execution, Skill, idempotence, preservation and exit codes passed on ${process.platform}/${process.arch}`);
} finally { fs.rmSync(temporary, {recursive:true, force:true}); }
