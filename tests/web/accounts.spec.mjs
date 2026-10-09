import { test, expect } from "playwright/test";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const baseURL = process.env.WKS_ACCOUNTS_BROWSER_URL || "http://127.0.0.1:8083";
test.use({ baseURL });

async function passwordLogin(page, password) {
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await page.evaluate((value) => {
    document.getElementById("username").value = "engenharia";
    document.getElementById("password").value = value;
  }, password);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
}
async function acceptRecovery(page) {
  await page.getByLabel("Salvei meu token de recuperação").check();
  await page.getByRole("button", { name: "Ir para minha biblioteca" }).click();
  await expect(page.locator("#workspace")).toBeVisible();
}

test("empty installation, password account, saved token, recovery and preserved library", async ({
  page,
  browser,
}) => {
  const password = crypto.randomBytes(24).toString("hex");
  const replacement = crypto.randomBytes(24).toString("hex");
  const failures = [];
  page.on("pageerror", (e) => failures.push(e.message));
  await page.goto("/app/");
  await expect(
    page.getByRole("heading", { name: "Crie sua primeira conta" }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(root, "docs/ui-workspace/07-primeira-conta.png"),
    fullPage: true,
  });
  await page.evaluate((value) => {
    document.getElementById("username").value = "engenharia";
    document.getElementById("password").value = value;
    document.getElementById("confirm-password").value = value;
  }, password);
  await page
    .getByRole("button", { name: "Criar primeira conta", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Guarde seu token de recuperação" }),
  ).toBeVisible();
  const token = await page
    .getByLabel("Seu token de recuperação", { exact: true })
    .inputValue();
  expect(token.startsWith("wks-recovery-")).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "Ir para minha biblioteca" }),
  ).toBeDisabled();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Baixar token", exact: true }).click();
  const download = await downloadPromise;
  const downloaded = fs.readFileSync(await download.path(), "utf8").trim();
  expect(downloaded === token).toBeTruthy();
  await acceptRecovery(page);
  await page
    .getByRole("button", { name: "Criar contexto", exact: true })
    .click();
  await page.getByLabel("Nome do contexto").fill("Biblioteca pessoal");
  await page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true })
    .click();
  await expect(page.locator("#editor")).not.toBeVisible();
  await expect(page.locator("#context-title")).toHaveText("Biblioteca pessoal");
  const identity = await (await page.request.get("/app/session")).json();
  const namespaces = await (await page.request.get("/v1/namespaces")).json();
  const receipt = await page.request.post("/v1/sources", {
    headers: {
      "X-WKS-CSRF": identity.csrf_token,
      "Idempotency-Key": "account-browser-source",
    },
    data: {
      namespace_id: namespaces.items[0].id,
      kind: "text",
      title: "Histórico da biblioteca",
      text: "Uma fonte preservada após recuperar a conta.",
    },
  });
  expect(receipt.status()).toBe(201);
  const source = (await receipt.json()).source_id;
  await page.getByRole("button", { name: "Encerrar sessão" }).click();
  await expect(page.getByLabel("Username", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Criar primeira conta", exact: true }),
  ).not.toBeVisible();
  await page.screenshot({
    path: path.join(root, "docs/ui-workspace/08-login.png"),
    fullPage: true,
  });
  await passwordLogin(page, "wrong-password");
  await expect(page.locator("#login-error")).toContainText(
    "Username ou senha inválidos",
  );
  await passwordLogin(page, password);
  await expect(page.locator("#workspace")).toBeVisible();
  const cookie = (await page.context().cookies()).find(
    (item) => item.name === "wks_session",
  );
  expect(Boolean(cookie?.httpOnly)).toBeTruthy();
  const guest = await browser.newContext({ baseURL });
  try {
    const recovery = await guest.newPage();
    await recovery.goto("/app/");
    await recovery
      .getByRole("button", { name: "Recuperar minha conta" })
      .click();
    await expect(
      recovery.getByLabel("Username", { exact: true }),
    ).not.toBeVisible();
    await recovery.evaluate(
      (value) => {
        document.getElementById("recovery-input").value = value.token;
        document.getElementById("new-password").value = value.password;
        document.getElementById("confirm-new-password").value = value.password;
      },
      { token, password: replacement },
    );
    await recovery
      .getByRole("button", { name: "Recuperar conta", exact: true })
      .click();
    await expect(
      recovery.getByRole("heading", {
        name: "Guarde seu token de recuperação",
      }),
    ).toBeVisible();
    const next = await recovery
      .getByLabel("Seu token de recuperação", { exact: true })
      .inputValue();
    expect(Boolean(next && next !== token)).toBeTruthy();
    await expect(
      recovery.getByLabel("Seu token de recuperação", { exact: true }),
    ).toHaveAttribute("type", "password");
    await recovery.screenshot({
      path: path.join(root, "docs/ui-workspace/09-recuperacao.png"),
      fullPage: true,
    });
    await acceptRecovery(recovery);
    expect((await recovery.request.get("/v1/sources/" + source)).status()).toBe(
      200,
    );
    expect(
      (
        await recovery.request.get("/v1/namespaces", {
          headers: { Cookie: "wks_session=" + cookie.value },
        })
      ).status(),
    ).toBe(401);
    const replay = await recovery.request.post("/app/recover", {
      data: { recovery_token: token, password },
    });
    expect(replay.status()).toBe(401);
    await recovery
      .getByRole("button", { name: "Token de recuperação", exact: true })
      .click();
    await recovery.evaluate((value) => {
      document.getElementById("edit-current-password").value = value;
    }, replacement);
    await recovery
      .locator("#editor")
      .getByRole("button", { name: "Salvar", exact: true })
      .click();
    await expect(recovery.locator("#editor")).not.toBeVisible();
    await expect(
      recovery.getByRole("heading", {
        name: "Guarde seu token de recuperação",
      }),
    ).toBeVisible();
    const rotated = await recovery
      .getByLabel("Seu token de recuperação", { exact: true })
      .inputValue();
    expect(Boolean(rotated && rotated !== next)).toBeTruthy();
    await acceptRecovery(recovery);
    await recovery.getByRole("button", { name: "Encerrar sessão" }).click();
    await passwordLogin(recovery, password);
    await expect(recovery.locator("#login-error")).toContainText(
      "Username ou senha inválidos",
    );
    await passwordLogin(recovery, replacement);
    await expect(recovery.locator("#workspace")).toBeVisible();
    expect(
      await recovery.evaluate(
        () => localStorage.length + sessionStorage.length,
      ),
    ).toBe(0);
  } finally {
    await guest.close();
  }
  expect(failures).toEqual([]);
});
