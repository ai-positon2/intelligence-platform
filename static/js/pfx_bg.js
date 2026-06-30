/* Shared WebGL particle background (extracted from Usage dashboard). Loads Three.js from CDN; skips on reduced-motion. */
(function(){if(window.__pfx)return;window.__pfx=1;
if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
var ORB=!!window.__PFX_ORB;
function start(){if(!window.THREE)return;var MOB=matchMedia('(pointer:coarse)').matches;
var cv=document.createElement('canvas');cv.id='pfx-bg';cv.style.cssText='position:fixed;inset:0;width:100%;height:100%;z-index:-1;pointer-events:none;opacity:0;transition:opacity 1.6s ease';
document.body.insertBefore(cv,document.body.firstChild);
var rnd;try{rnd=new THREE.WebGLRenderer({canvas:cv,antialias:true,alpha:true});}catch(e){cv.remove();return;}
rnd.setPixelRatio(Math.min(devicePixelRatio,2));rnd.setSize(innerWidth,innerHeight);
var scn=new THREE.Scene();var cam=new THREE.PerspectiveCamera(60,innerWidth/innerHeight,0.1,200);cam.position.z=13;
var grp=new THREE.Group();scn.add(grp);var clk=new THREE.Clock();
var cA=new THREE.Color(0x22d3ee),cB=new THREE.Color(0x8b5cf6),cC=new THREE.Color(0xa5b4fc),tm=new THREE.Color();
var VS=['attribute vec3 aColor;attribute float aSize;uniform float uSize;uniform float uTime;varying vec3 vC;varying float vT;','void main(){vC=aColor;vT=0.6+0.4*sin(uTime*1.3+aSize*9.0);vec4 mv=modelViewMatrix*vec4(position,1.0);gl_PointSize=aSize*uSize*(300.0/-mv.z);gl_Position=projectionMatrix*mv;}'].join('\n');
var FS=['varying vec3 vC;varying float vT;','void main(){float d=length(gl_PointCoord-0.5);float c=smoothstep(0.46,0.33,d);float g=smoothstep(0.5,0.0,d);float a=pow(c,1.15)+pow(g,4.2)*0.13;gl_FragColor=vec4(vC*vT,a);}'].join('\n');
function mkMat(s){return new THREE.ShaderMaterial({uniforms:{uSize:{value:s},uTime:{value:0}},transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,vertexShader:VS,fragmentShader:FS});}
// subtle ambient field (large shell so center stays clear)
var N=MOB?460:1000;
var pos=new Float32Array(N*3),base=new Float32Array(N*3),col=new Float32Array(N*3),sz=new Float32Array(N),rr=new Float32Array(N*2);
for(var i=0;i<N;i++){var u=Math.random(),v=Math.random(),th=u*6.2832,ph=Math.acos(2*v-1),r=8+Math.random()*6;
base[i*3]=r*Math.sin(ph)*Math.cos(th);base[i*3+1]=r*Math.sin(ph)*Math.sin(th)*0.92;base[i*3+2]=r*Math.cos(ph);
pos[i*3]=base[i*3];pos[i*3+1]=base[i*3+1];pos[i*3+2]=base[i*3+2];
var m=Math.random();tm.copy(m<.5?cA:cB).lerp(cC,Math.random()*.5);col[i*3]=tm.r;col[i*3+1]=tm.g;col[i*3+2]=tm.b;
sz[i]=(Math.random()*Math.random())*0.85+0.45;rr[i*2]=Math.random()*6.28;rr[i*2+1]=0.22+Math.random()*0.45;}
var geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(pos,3));geo.setAttribute('aColor',new THREE.BufferAttribute(col,3));geo.setAttribute('aSize',new THREE.BufferAttribute(sz,1));
var uni={uSize:MOB?0.6:0.5};var mat=mkMat(uni.uSize);var pts=new THREE.Points(geo,mat);grp.add(pts);var fU=mat.uniforms.uTime;
var lines=null,ed=[],lp=null;
if(!MOB){var CN=150,K=2;for(var a=0;a<CN;a++){var ai=(a*6+1)%N,bs=[];for(var b=0;b<N;b+=5){if(b===ai)continue;var dx=base[ai*3]-base[b*3],dy=base[ai*3+1]-base[b*3+1],dz=base[ai*3+2]-base[b*3+2];bs.push([dx*dx+dy*dy+dz*dz,b]);}bs.sort(function(p,q){return p[0]-q[0];});for(var k=0;k<K;k++)ed.push([ai,bs[k][1]]);}
lp=new Float32Array(ed.length*6);var lg=new THREE.BufferGeometry();lg.setAttribute('position',new THREE.BufferAttribute(lp,3));var lm=new THREE.LineBasicMaterial({color:0x3bc9e6,transparent:true,opacity:0.03,blending:THREE.AdditiveBlending,depthWrite:false});lines=new THREE.LineSegments(lg,lm);grp.add(lines);}
// focal cursor-reactive orb (hub/gtm/seo)
var orb=null,oN=0,opos=null,obase=null,orr=null,oU=null;
if(ORB){oN=MOB?360:680;opos=new Float32Array(oN*3);obase=new Float32Array(oN*3);var ocol=new Float32Array(oN*3);var osz=new Float32Array(oN);orr=new Float32Array(oN*2);
for(var j=0;j<oN;j++){var ju=Math.random(),jv=Math.random(),jt=ju*6.2832,jp=Math.acos(2*jv-1),jr=1.7+Math.pow(Math.random(),0.7)*1.9;
obase[j*3]=jr*Math.sin(jp)*Math.cos(jt);obase[j*3+1]=jr*Math.sin(jp)*Math.sin(jt);obase[j*3+2]=jr*Math.cos(jp);
opos[j*3]=obase[j*3];opos[j*3+1]=obase[j*3+1];opos[j*3+2]=obase[j*3+2];
var jm=Math.random();tm.copy(jm<.5?cA:cB).lerp(cC,Math.random()*.6);ocol[j*3]=tm.r;ocol[j*3+1]=tm.g;ocol[j*3+2]=tm.b;
osz[j]=Math.random()*0.9+0.65;orr[j*2]=Math.random()*6.28;orr[j*2+1]=0.4+Math.random()*0.7;}
var ogeo=new THREE.BufferGeometry();ogeo.setAttribute('position',new THREE.BufferAttribute(opos,3));ogeo.setAttribute('aColor',new THREE.BufferAttribute(ocol,3));ogeo.setAttribute('aSize',new THREE.BufferAttribute(osz,1));
var omat=mkMat(MOB?0.95:0.9);orb=new THREE.Points(ogeo,omat);scn.add(orb);oU=omat.uniforms.uTime;}
var px=0,py=0,tx=0,ty=0,hasM=0,mWx=0,mWy=0;
addEventListener('mousemove',function(e){tx=e.clientX/innerWidth-.5;ty=e.clientY/innerHeight-.5;hasM=1;var hh=Math.tan(60*Math.PI/360)*13;var hw=hh*(innerWidth/innerHeight);mWx=tx*2*hw;mWy=-ty*2*hh;});
addEventListener('resize',function(){cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();rnd.setSize(innerWidth,innerHeight);});
requestAnimationFrame(function loop(){var t=clk.getElapsedTime();fU.value=t;if(oU)oU.value=t;
for(var i=0;i<N;i++){var ph=rr[i*2]+t*rr[i*2+1];pos[i*3]=base[i*3]+Math.sin(ph)*0.2;pos[i*3+1]=base[i*3+1]+Math.cos(ph*0.9)*0.2;pos[i*3+2]=base[i*3+2]+Math.sin(ph*0.7)*0.2;}
geo.attributes.position.needsUpdate=true;
if(lines){for(var e=0;e<ed.length;e++){var i0=ed[e][0],i1=ed[e][1],o=e*6;lp[o]=pos[i0*3];lp[o+1]=pos[i0*3+1];lp[o+2]=pos[i0*3+2];lp[o+3]=pos[i1*3];lp[o+4]=pos[i1*3+1];lp[o+5]=pos[i1*3+2];}lines.geometry.attributes.position.needsUpdate=true;}
if(orb){for(var j=0;j<oN;j++){var oph=orr[j*2]+t*orr[j*2+1];var bx=obase[j*3]+Math.sin(oph)*0.1,by=obase[j*3+1]+Math.cos(oph*0.9)*0.1,bz=obase[j*3+2]+Math.sin(oph*0.7)*0.1;
if(hasM){var ddx=bx-mWx,ddy=by-mWy;var dist2=ddx*ddx+ddy*ddy;if(dist2<9){var dd=Math.sqrt(dist2)+0.001;var pu=(3-dd)/3;pu=pu*pu*2.0;bx+=ddx/dd*pu;by+=ddy/dd*pu;}}
opos[j*3]=bx;opos[j*3+1]=by;opos[j*3+2]=bz;}orb.geometry.attributes.position.needsUpdate=true;}
px+=(tx-px)*0.04;py+=(ty-py)*0.04;grp.rotation.y=t*0.02+px*0.32;grp.rotation.x=py*0.2;
cam.position.x+=(px*0.9-cam.position.x)*0.03;cam.position.y+=(-py*0.6-cam.position.y)*0.03;cam.lookAt(0,0,0);
rnd.render(scn,cam);requestAnimationFrame(loop);});
setTimeout(function(){cv.style.opacity=MOB?'0.12':'0.18';},80);}
function ens(cb){if(window.THREE)return cb();var s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js';s.onload=cb;s.onerror=function(){};document.head.appendChild(s);}
if(document.readyState!=='loading')ens(start);else addEventListener('DOMContentLoaded',function(){ens(start);});
})();
