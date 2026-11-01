import type { Page } from "@playwright/test";

export async function mockGoogleScript(page: Page) {
  await page.route("https://accounts.google.com/gsi/client", (route) =>
    route.fulfill({
      contentType: "text/javascript",
      body: `
    window.google={accounts:{id:{
      initialize(config){window.testGoogleConfig=config;},
      renderButton(element){
        const button=document.createElement('button');button.textContent='Continue with Google';
        button.onclick=async()=>{
          const config=window.testGoogleConfig;
          const query=new URLSearchParams({nonce:config.nonce,email:window.testGoogleEmail||'popup@college.edu'});
          const response=await fetch('/api/test/google-credential?'+query);
          config.callback(await response.json());
        };element.replaceChildren(button);
      },
      disableAutoSelect(){window.testAutoSelectDisabled=true;}
    }}};
  `,
    }),
  );
}
