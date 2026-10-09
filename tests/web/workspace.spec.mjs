import { test, expect } from "playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const token = fs
  .readFileSync(
    process.env.WKS_BROWSER_TOKEN_FILE ||
      path.join(root, ".local/browser-token"),
    "utf8",
  )
  .trim();
const screenshots = path.join(root, "docs/ui-workspace");
fs.mkdirSync(screenshots, { recursive: true });
async function login(page) {
  await page.goto("/app/");
  await expect(page.getByLabel("Credencial de acesso")).toBeVisible();
  await page.evaluate((value) => {
    document.getElementById("token").value = value;
  }, token);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page.locator("#workspace")).toBeVisible();
  await expect(page.locator(".metric strong").first()).toBeVisible();
  await page
    .getByLabel("Contexto de conhecimento", { exact: true })
    .selectOption({ label: "Conhecimento de engenharia" });
  await expect(page.locator("#context-title")).toHaveText(
    "Conhecimento de engenharia",
  );
  await expect(page.locator("#metrics .metric strong").first()).toBeVisible();
  await expect(page.locator("#recent-sources")).not.toContainText(
    "Carregando biblioteca",
  );
}
async function search(page, query) {
  await expect
    .poll(
      async () => {
        const identity = await (await page.request.get("/app/session")).json();
        const response = await page.request.post("/v1/search", {
          headers: { "x-wks-csrf": identity.csrf_token },
          data: { query },
        });
        return (await response.json()).items?.length || 0;
      },
      { timeout: 45000 },
    )
    .toBeGreaterThan(0);
  await page
    .locator(".tabs")
    .getByRole("tab", { name: "Busca", exact: false })
    .click();
  await page.getByLabel("Buscar passagens nas fontes").fill(query);
  await page
    .locator("#search-form")
    .getByRole("button", { name: "Buscar", exact: true })
    .click();
  await expect(page.locator(".result").first()).toBeVisible();
}

test("WOS pattern: summary, server counts, pagination, search, versions, original and media", async ({
  page,
}) => {
  const failures = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await login(page);
  await expect(page.locator(".metric strong").first()).toHaveText("26");
  await page.screenshot({
    path: path.join(screenshots, "01-resumo.png"),
    fullPage: true,
  });
  await page
    .locator(".tabs")
    .getByRole("tab", { name: "Fontes", exact: false })
    .click();
  await expect(page.locator("#source-list .source-row")).toHaveCount(20);
  await page.getByRole("button", { name: "Carregar mais fontes" }).click();
  await expect(page.locator("#source-list .source-row")).toHaveCount(26);
  await page.screenshot({
    path: path.join(screenshots, "02-fontes.png"),
    fullPage: true,
  });
  await search(page, '"circuitos em paralelo"');
  await expect(page.locator("#search-results")).toContainText("Texto nativo");
  await page.screenshot({
    path: path.join(screenshots, "03-busca.png"),
    fullPage: true,
  });
  await page
    .locator(".result")
    .first()
    .getByRole("button", { name: "Ler na fonte" })
    .click();
  await expect(page.locator("#detail")).toBeVisible();
  await expect(page.locator("#blocks")).toContainText("tensão");
  await expect(
    page.getByRole("link", { name: "Baixar original", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(screenshots, "04-leitura.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Fechar detalhe" }).click();
  await search(page, '"Manual de circuitos"');
  await page
    .locator(".result")
    .first()
    .getByRole("button", { name: "Ler na fonte" })
    .click();
  await expect(
    page.getByRole("button", { name: "Abrir mídia da passagem" }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Abrir mídia da passagem" })
    .first()
    .click();
  await expect(page.locator("#blocks img")).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator("#blocks img")
        .evaluate((image) => image.complete && image.naturalWidth > 0),
    )
    .toBeTruthy();
  await page.screenshot({
    path: path.join(screenshots, "05-midia.png"),
    fullPage: true,
  });
  const storage = await page.evaluate(() => ({
    local: localStorage.length,
    session: sessionStorage.length,
  }));
  expect(storage).toEqual({ local: 0, session: 0 });
  expect(failures).toEqual([]);
});

test("real browser source and collection mutations, pinned history and safe text rendering", async ({
  page,
}) => {
  await login(page);
  const title = "Teste da biblioteca " + Date.now();
  const collectionTitle = "Circuitos da jornada " + Date.now();
  await page
    .getByRole("button", { name: "Adicionar fonte", exact: true })
    .first()
    .click();
  await page.getByLabel("Título da fonte").fill(title);
  await page
    .getByLabel("Conteúdo", { exact: true })
    .fill(
      '# Referência\nCapacitância entre placas. <img src=x onerror="window.injection=true">',
    );
  await page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true })
    .click();
  await expect(page.locator("#editor")).not.toBeVisible();
  await search(page, title);
  await page
    .locator(".result")
    .first()
    .getByRole("button", { name: "Ler na fonte" })
    .click();
  await expect(page.locator("#blocks")).toContainText("Capacitância");
  expect(await page.evaluate(() => window.injection)).toBeUndefined();
  await page
    .getByRole("button", { name: "Adicionar versão", exact: true })
    .click();
  await page
    .getByLabel("Conteúdo da nova versão")
    .fill("Indutância com histórico preservado.");
  await page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true })
    .click();
  await expect(page.locator("#editor")).not.toBeVisible();
  await expect
    .poll(
      async () => {
        await page
          .getByRole("button", { name: "Atualizar fonte", exact: true })
          .click();
        await expect(page.locator("#blocks"))
          .toContainText("Indutância", { timeout: 2000 })
          .catch(() => {});
        return page.locator("#blocks").textContent();
      },
      { timeout: 45000 },
    )
    .toContain("Indutância");
  await page.getByLabel("Versão da fonte").selectOption({ label: "Versão 1" });
  await expect(page.locator("#blocks")).toContainText("Capacitância");
  await page.getByRole("button", { name: "Fechar detalhe" }).click();
  await page
    .locator(".tabs")
    .getByRole("tab", { name: "Coleções", exact: false })
    .click();
  await page
    .getByRole("button", { name: "Criar coleção", exact: true })
    .click();
  await page.getByLabel("Nome da coleção").fill(collectionTitle);
  await page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true })
    .click();
  await expect(
    page.locator(".collection-card").filter({ hasText: collectionTitle }),
  ).toBeVisible();
  await search(page, title);
  await page
    .locator(".result")
    .first()
    .getByRole("button", { name: "Ler na fonte" })
    .click();
  await page
    .getByLabel("Coleção para esta fonte")
    .selectOption({ label: collectionTitle });
  await page
    .locator("#detail")
    .getByRole("button", { name: "Adicionar", exact: true })
    .click();
  await expect(page.locator("#notice")).toContainText("adicionada");
  await page.getByRole("button", { name: "Fechar detalhe" }).click();
  await page
    .locator("#collection-nav")
    .getByRole("button", { name: collectionTitle, exact: true })
    .click();
  await expect(page.locator("#source-list")).toContainText(title);
});

test("mobile layout, real upload, processing and logout", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: path.join(screenshots, "06-mobile.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Adicionar fonte", exact: true })
    .first()
    .click();
  const title = "Arquivo da jornada " + Date.now();
  await page.getByLabel("Título da fonte").fill(title);
  await page.getByLabel("Tipo da fonte").selectOption("upload");
  await page
    .getByLabel("Arquivo original")
    .setInputFiles(path.join(root, "tests/fixtures/circuit.png"));
  await page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true })
    .click();
  await expect(page.locator("#editor")).not.toBeVisible();
  await expect
    .poll(
      async () => {
        const headers = {
          "x-wks-csrf": (await (await page.request.get("/app/session")).json())
            .csrf_token,
        };
        const response = await page.request.post("/v1/search", {
          headers,
          data: { query: title },
        });
        return (await response.json()).items?.length || 0;
      },
      { timeout: 45000 },
    )
    .toBeGreaterThan(0);
  await search(page, title);
  await page
    .locator(".result")
    .first()
    .getByRole("button", { name: "Ler na fonte" })
    .click();
  await expect(
    page.getByRole("link", { name: "Baixar original", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Fechar detalhe" }).click();
  await page.getByRole("button", { name: "Encerrar sessão" }).click();
  await expect(page.getByLabel("Credencial de acesso")).toBeVisible();
  expect((await page.request.get("/v1/namespaces")).status()).toBe(401);
});

test("retry after a lost response reuses the receipt and creates one context", async ({
  page,
}) => {
  await login(page);
  const title = `Recibo da jornada ${Date.now()}`;
  const keys = [];
  let loseResponse = true;
  await page.route("**/v1/namespaces", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    keys.push(route.request().headers()["idempotency-key"]);
    if (loseResponse) {
      loseResponse = false;
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      return route.abort("failed");
    }
    return route.continue();
  });
  await page
    .getByRole("button", { name: "Criar contexto", exact: true })
    .click();
  await page.getByLabel("Nome do contexto").fill(title);
  const save = page
    .locator("#editor")
    .getByRole("button", { name: "Salvar", exact: true });
  await save.click();
  await expect(page.locator("#editor-error")).toContainText(
    "Conexão interrompida",
  );
  await save.click();
  await expect(page.locator("#editor")).not.toBeVisible();
  await expect(page.locator("#context-title")).toHaveText(title);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  const response = await page.request.get("/v1/namespaces?page_size=50");
  expect(
    (await response.json()).items.filter((item) => item.title === title),
  ).toHaveLength(1);
});
