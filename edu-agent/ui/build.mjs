import {build} from 'esbuild';
await build({entryPoints:['src.tsx'],bundle:true,minify:true,format:'esm',target:['es2022'],jsx:'automatic',outfile:'../frontend/enhancements.js',legalComments:'eof',define:{'process.env.NODE_ENV':'"production"'},plugins:[{name:'shared-native-connector',setup(builder){builder.onResolve({filter:/frontend\/api\.js$/},()=>({path:'/api.js',external:true}));}}]});
console.log('Presentation islands built.');
