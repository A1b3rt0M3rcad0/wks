#!/usr/bin/env node
'use strict';
const { spawnSync } = require('node:child_process');
const path = require('node:path');
const systems = { linux: 'linux', darwin: 'darwin', win32: 'windows' };
const architectures = { x64: 'amd64', arm64: 'arm64' };
const system = systems[process.platform];
const arch = architectures[process.arch];
if (!system || !arch) {
  console.error(`wksctl: unsupported platform ${process.platform}/${process.arch}`);
  process.exit(2);
}
const executable = path.join(__dirname, '..', 'native', `${system}-${arch}`, system === 'windows' ? 'wksctl.exe' : 'wksctl');
const result = spawnSync(executable, process.argv.slice(2), { stdio: 'inherit' });
if (result.error) {
  console.error(`wksctl: cannot execute bundled client: ${result.error.message}`);
  process.exit(2);
}
if (result.signal) {
  process.kill(process.pid, result.signal);
} else {
  process.exit(result.status ?? 2);
}
