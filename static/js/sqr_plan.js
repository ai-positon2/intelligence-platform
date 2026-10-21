/* Search Query Analysis: how to apply what the analysis found, and what it is worth. No page code.
 *
 * sqr_core.js says which negatives and keywords to add; this says where to click to add them, in what form, and what the negatives are worth a month: the money spent
 * in the range on terms that never converted, scaled to 30 days. Every figure is arithmetic on the analysis' own numbers, and the worth says what it assumes: the same
 * searches come back at the same cost, and the money is a saving only if the budget comes down (otherwise it goes to other searches).
 * tests/test_sqr_plan_js.py runs this file in Node.
 */
(function(root){
"use strict";

var MONTH = 30, NAMES = 3;

function isAccount(n){return String(n.level||"").indexOf("Account")===0;}
function days(range){
  if(!range) return null;
  var a=Date.parse(range[0]), b=Date.parse(range[1]);
  if(isNaN(a)||isNaN(b)||b<a) return null;
  return Math.round((b-a)/86400000)+1;
}
function plural(n,one,many){return n+" "+(n===1?one:many||one+"s");}
function names(list){
  var u=[]; list.forEach(function(x){if(x&&u.indexOf(x)<0) u.push(x);});
  var shown=u.slice(0,NAMES).map(function(x){return '"'+x+'"';}).join(", ");
  return shown+(u.length>NAMES?" and "+(u.length-NAMES)+" more":"");
}

/* The priority negatives' worth over a month. `money` formats a number in the account's currency. Null when the range is not known (terms read from files). */
function saving(r,range,money){
  var d=days(range);
  if(!d) return null;
  var waste=function(list){return list.filter(function(n){return !n.routing;}).reduce(function(s,n){return s+(n.wasted||0);},0);};
  var sure=waste(r.priorityNegatives||[]), maybe=waste(r.reviewNegatives||[]);
  if(sure<=0) return null;
  var text="These negatives would have kept about "+money(sure*MONTH/d)+" a month from terms that never converted ("+money(sure)+" over the "+plural(d,"day")+" read)";
  if(maybe>0) text+=", and up to "+money(maybe*MONTH/d)+" more if the ones to check first prove safe";
  return text+". It assumes the same searches keep coming at the same cost. The money is a saving only if the budget comes down; left in the budget it goes to other searches, which is worth more only if those convert better.";
}

function negativePlan(r,range,money){
  var list=r.priorityNegatives||[];
  if(!list.length) return null;
  var acct=list.filter(isAccount), camp=list.filter(function(n){return !isAccount(n)&&!n.routing;}), route=list.filter(function(n){return n.routing;});
  var steps=["Take the rows from the table below (or the sheet Negatives to Add (Priority) in the workbook). Each row has the negative, its match type and where it goes."];
  if(acct.length) steps.push("Account level ("+plural(acct.length,"negative")+"): in Google Ads open Campaigns, then Keywords, then Negative keywords, click the +, and choose the account level (if it is not offered, use Admin, then Account settings, then Negative keywords). "+
    "Paste them one per line with the match type written in: [exact], \"phrase\", or plain words for broad.");
  if(camp.length) steps.push("Campaign level ("+plural(camp.length,"negative")+" in "+names(camp.map(function(n){return n.campaign;}))+"): open Campaigns, click the campaign, then Keywords, then Negative keywords, click the +, choose the campaign, and paste only that campaign's rows.");
  if(route.length) steps.push("Brand searches in a non-brand campaign ("+plural(route.length,"negative")+"): add each as a campaign-level negative in the campaign named under Where, so those searches go to the Brand campaign. Check the Brand campaign bids on the term first, or they will go nowhere.");
  if(list.length>30) steps.push("With this many, paste them into Google Ads Editor instead (Edit, then Make multiple changes, then Add/update negative keywords), review them there and post once.");
  steps.push("Run this analysis again after a day or two: terms the negatives cover should drop out, and what is left is what still needs a decision.");
  var worth=saving(r,range,money);
  return {steps:steps,worth:worth};
}

function checkPlan(r){
  var list=r.reviewNegatives||[];
  if(!list.length) return null;
  return {steps:["For each row, read what is named under Check: the terms that converted, the relevant terms it would also stop, or the keyword it would block.",
    "If the terms that converted were a one-off, add the negative as written. If it would stop relevant terms, make it narrower: an exact match for the exact search instead of a phrase, or at campaign level in the campaign where the term does not belong.",
    "If it would stop a keyword you bid on, leave the negative out, or lower that keyword's bid instead of blocking it. If it names a competitor you bid on on purpose, leave it out.",
    "Add the ones you settle the same way as the priority negatives (the other tab), and write down the ones you decide against so they are not argued again next month."],worth:null};
}

function keywordPlan(r){
  var list=r.priorityKeywords||[];
  if(!list.length) return null;
  var kw=list.filter(function(k){return k.assetType==="Keyword";}), th=list.filter(function(k){return k.assetType!=="Keyword";});
  var steps=[];
  if(kw.length) steps.push("Keywords ("+kw.length+"): in Google Ads open Campaigns, then Keywords, then Search keywords, click the +, choose the ad group named under Where to add, and paste the terms with the match type written in: [exact] or \"phrase\" as the table says.");
  if(th.length) steps.push("Search themes ("+th.length+", Performance Max): open the campaign, then Asset groups, open the asset group named under Where to add, and add the terms under Search themes (they take no match type).");
  steps.push("Add the priority negatives first: the keywords were checked against them, and a new keyword that a negative blocks would never show.");
  steps.push("New keywords take the bidding of their ad group. Look at their cost per conversion after two weeks, and pause any that spend without converting.");
  return {steps:steps,worth:null};
}

var api={days:days,saving:saving,negativePlan:negativePlan,checkPlan:checkPlan,keywordPlan:keywordPlan};
root.SQRPlan=api;
if(typeof module!=="undefined"&&module.exports) module.exports=api;
})(typeof window!=="undefined"?window:globalThis);
