<#-- PalletWise sign-in: our own page; Keycloak checks the password, the form posts to it. -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>Sign in to PalletWise</title>
  <link rel="icon" href="${url.resourcesPath}/img/favicon.ico" sizes="48x48">
  <link rel="apple-touch-icon" href="${url.resourcesPath}/img/apple-touch-icon.png">
  <link rel="stylesheet" href="${url.resourcesPath}/css/signin.css">
</head>
<body>
  <main class="pw-page">
    <section class="pw-card" aria-labelledby="pw-title">
      <div class="pw-art" aria-hidden="true">
        <canvas id="pw-floor"></canvas>
        <div class="pw-brand">
          <img src="${url.resourcesPath}/img/logo.webp" alt="" class="pw-logo">
          <p class="pw-name">PalletWise</p>
          <p class="pw-tagline">The warehouse copilot: every location, order and exception, answered on the map.</p>
        </div>
      </div>

      <div class="pw-form-side">
        <h1 id="pw-title">Welcome back</h1>
        <p class="pw-sub">Sign in to your warehouse</p>

        <#if message?has_content && (message.type != 'warning' || !isAppInitiatedAction??)>
          <p class="pw-alert pw-alert-${message.type}" role="alert">${kcSanitize(message.summary)?no_esc}</p>
        </#if>

        <form id="kc-form-login" action="${url.loginAction}" method="post" onsubmit="document.getElementById('kc-login').disabled = true; return true;">
          <label class="pw-field">
            <span>Username or email</span>
            <input id="username" name="username" type="text" value="${(login.username!'')}" autocomplete="username" autofocus
                   aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>">
          </label>
          <label class="pw-field">
            <span>Password</span>
            <span class="pw-password">
              <input id="password" name="password" type="password" autocomplete="current-password"
                     aria-invalid="<#if messagesPerField.existsError('username','password')>true</#if>">
              <button type="button" class="pw-reveal" id="pw-reveal" aria-label="Show password" aria-pressed="false">Show</button>
            </span>
          </label>
          <#if messagesPerField.existsError('username','password')>
            <p class="pw-alert pw-alert-error" role="alert">${kcSanitize(messagesPerField.getFirstError('username','password'))?no_esc}</p>
          </#if>
          <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>>
          <button class="pw-submit" name="login" id="kc-login" type="submit">Sign in <span aria-hidden="true">&rarr;</span></button>
        </form>

        <div class="pw-demo" id="pw-demo" hidden>
          <p class="pw-demo-title">Demo accounts on this installation</p>
          <div class="pw-demo-cards" id="pw-demo-cards"></div>
        </div>
        <p class="pw-note">PalletWise never sees your password: it goes straight to the identity provider.</p>
      </div>
    </section>
  </main>
  <script src="${url.resourcesPath}/js/demo-accounts.js"></script>
  <script src="${url.resourcesPath}/js/signin.js"></script>
</body>
</html>
