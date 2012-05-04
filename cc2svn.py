#!/usr/bin/env python
# -*- coding: utf-8 -*-
#===============================================================================
# The MIT License
# 
# Copyright (c) 2009 Vadim Goryunov
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#===============================================================================
"""
NAME
    cc2svn.py - converts ClearCase view files to SVN dump
    The dump can be loaded by SVN using 'cat svndump.txt | svnadmin load' command.    

SYNOPSIS
    cc2svn.py -run | -help

DESCRIPTION
    The tool uses the current ClearCase view to list the CC history (ct lshi -rec)
    then it goes through the history and processes each record.        
    That means that the tool does not transfer those files that are not visible from the current CC view.
    However the tool transfers all CC labels to SVN tags correctly. For that in the second phase 
    it sets config_spec of the current view to match the label (element * LABEL) for each given label 
    and checks that no files are lost during the first phase.
    WARNING: Side effect - the tool changes the config_spec of the current working ClearCase view.
             Do not use the view during the tool work.  
     
    All branches except the /main are created using 'svn cp' command basing on the CC parent branch.
    There is a difference in creating the branches in ClearCase and SVN.
    SVN copies all files from parent branch to the target like: svn cp branches/main branches/dev_branch
    ClearCase creates the actual branch for file upon checkout operation only.
    In other words the tool can't guarantee the content of /branches will be exactly like in ClearCase.    
    But the tool guarantees the labels are transferred correctly.
    
    The tool uses cache directory to place ClearCase version files there. The cache speeds up the transfer process
    in many times in subsequent attempts (up to 10 times). It may be recommended to start the tool 2 days before the
    actual transfer loading all files to the cache. So only new versions appeared during these days will be retrieved from
    ClearCase in the day of the transfer.        
    Actually the tool caches any data retrieved from ClearCase including the history file.
    
    The tool provides the possibility to retry/ignore any ClearCase command if error occurs.
    The tool will put empty file to the cache if you ignore ClearCase retrieving operation error.
    Make sure you know what you are doing when ignoring the error.
    
    See config.py for options description. 
    
    Timing: CC repository of 5 GB (~120.000 revisions) is converted in ~1 hour using the pre-cached files.

COMMAND LINE OPTIONS
    -run    starts the tool
    -help   prints this help
   
FILES          
    ./config.py            main configuration file written like a python module.
    ./config.autoprops     extension -> svn properties mapping. See config.py for details
    
AUTHOR
    Vadim Goryunov (vadim.goryunov@gmail.com)
    
LICENSING
    cc2svn.py is distributed under the MIT license.
"""

from __future__ import with_statement
import os, subprocess, time, sys, hashlib, codecs, fnmatch

USAGE = "Usage: %(cmd)s -run | -help" % { "cmd" : sys.argv[0] } 

if len(sys.argv) <= 1:
    print USAGE
    sys.exit(1)

if sys.argv[1] == "-help":
    print __doc__
    sys.exit(0)

if sys.argv[1] != "-run":
    print USAGE
    sys.exit(1)    

############# constants ######################
    
HISTORY_FIELD_SEPARATOR = "@@@"    

HISTORY_FORMAT = "%Nd;%En;%Vn;%o;%l;%a;%m;%u;%Nc;\n".replace(";", HISTORY_FIELD_SEPARATOR)    

CC_DATE_FORMAT = "%Y%m%d.%H%M%S"
SVN_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S.000000Z"

FILEREAD_CHUNKSIZE = 512

############# parameters ######################
CC_LABELS_FILE = None
CC_BRANCHES_FILE = None
DUMP_SINCE_DATE = None

from config import *

CLEARTOOL = os.path.realpath(CLEARTOOL)
CC_VOB_DIR = os.path.realpath(CC_VOB_DIR)
CACHE_DIR = os.path.realpath(CACHE_DIR)
SVN_AUTOPROPS_FILE = os.path.realpath(SVN_AUTOPROPS_FILE)
SVN_DUMP_FILE = os.path.realpath(SVN_DUMP_FILE)
HISTORY_FILE = os.path.realpath(HISTORY_FILE)

if CC_LABELS_FILE:
    CC_LABELS_FILE = os.path.realpath(CC_LABELS_FILE)    
    
if CC_BRANCHES_FILE:
    CC_BRANCHES_FILE = os.path.realpath(CC_BRANCHES_FILE)

if DUMP_SINCE_DATE:            
    DUMP_SINCE_DATE = time.strptime(DUMP_SINCE_DATE, CC_DATE_FORMAT)

CCVIEW_TMPFILE = CACHE_DIR + "/label_config_spec_tmp_cc2svnpy"
CCVIEW_CONFIGSPEC = CACHE_DIR + "/user_config_spec_tmp_cc2svnpy"

############# utilities ######################

def logMessage(text):
    print time.strftime("%Y/%m/%d %H:%M:%S:"), text

def info(text):
    logMessage("INFO: " + text)

def warn(text):
    logMessage("WARNING: " + text)

def error(text):
    logMessage("ERROR: " + text)


def shellCmd(cmd, cwd=None, outfile=None):
    outfd = subprocess.PIPE
    outStr = ""
    status = ""
    while True:
        try:
            if outfile: 
                outfd = open(outfile, 'wb')
            if cwd and not os.path.exists(cwd):
                raise RuntimeError("No such file or directory: '" + cwd + "'")
            p = subprocess.Popen(cmd, cwd=cwd, stdout=outfd, stderr=subprocess.PIPE, shell=True, close_fds=True)
            (outStr, errStr) = p.communicate()
            if outfile:
                outfd.close()
            if p.returncode != 0:
                raise RuntimeError("Exit code: " + str(p.returncode) + "\n" + errStr)
            if len(errStr) > 0:
                raise RuntimeError("Command has non-empty error stream: \n" + errStr)
        except:
            error("Command failed: " + cmd + "\n" + str(sys.exc_info()[1]))
            status = askRetryContinueExit()
            if status == "retry": continue
        break
    return (status, outStr)

gIgnoreAll = False
def askRetryContinueExit():
    global gIgnoreAll
    if gIgnoreAll:
        return "ignore"    
    while True:
        print "\nRetry/Ignore/IgnoreAll/Exit? [r/i/a/x] (r:Enter): ",
        answer = sys.stdin.readline().strip()
        if answer == "" or answer == "r": return "retry"
        if answer == "i": return "ignore"
        if answer == "a": 
            gIgnoreAll = True
            return "ignore"
        if answer == "x": sys.exit(1)
        
def askYesNo(question):    
    while True:
        print "\n"+question+" [y/n] (y:Enter): ",
        answer = sys.stdin.readline().strip()
        if answer == "" or answer == "y": return True
        if answer == "n": return False

def toUTF8(text):
    return codecs.utf_8_encode(text)[0]

def rblocks(f, blocksize=4096):
    """Read file as series of blocks from end of file to start.

    The data itself is in normal order, only the order of the blocks is reversed.
    ie. "hello world" -> ["ld","wor", "lo ", "hel"]
    Note that the file must be opened in binary mode.
    """
    if 'b' not in f.mode.lower():
        raise Exception("File must be opened using binary mode.")
    size = os.stat(f.name).st_size
    fullblocks, lastblock = divmod(size, blocksize)

    # The first(end of file) block will be short, since this leaves 
    # the rest aligned on a blocksize boundary.  This may be more 
    # efficient than having the last (first in file) block be short
    f.seek(-lastblock,2)
    yield f.read(lastblock)

    for i in xrange(fullblocks-1,-1, -1):
        f.seek(i * blocksize)
        yield f.read(blocksize)

def rlines(f, keepends=False):
    """Iterate through the lines of a file in reverse order.

    If keepends is true, line endings are kept as part of the line.
    """
    buf = ''
    for block in rblocks(f):
        buf = block + buf
        lines = buf.splitlines(keepends)
        # Return all lines except the first (since may be partial)
        if lines:
            lines.reverse()
            buf = lines.pop() # Last line becomes end of new first line.
            for line in lines:
                yield line
    yield buf  # First line.
    
############# heart of the script ######################

class SvnProperties:
    def __init__(self):
        self.keyset = {}
        self.totalLen = 10 # len('PROPS-END\n')
        
    def reset(self):
        self.keyset.clear()
        self.totalLen = 10
        
    def set(self, key, value):
        if self.keyset.has_key(key): # this will probably not happen
            self.totalLen -= self.calcPropLength(key, self.keyset.get(key))
        self.keyset[key] = value
        self.totalLen += self.calcPropLength(key, value)
        
    def calcPropLength(self, key, value):
        klen = len("K " + str(len(key)) + "\n" + key + "\n")
        vlen = len("V " + str(len(value)) + "\n" + value + "\n")
        return klen + vlen
    
    def writeLength(self, out):
        out.write("Prop-content-length: " + str(self.totalLen) + "\n");
    
    def writeContent(self, out):
        for key,value in self.keyset.iteritems():
            out.write("K " + str(len(key)) + "\n");
            out.write(key + "\n");
            out.write("V " + str(len(value)) + "\n");
            if value: out.write(value)
            out.write("\n")        
        out.write("PROPS-END\n");
    
    def dump(self, out):
        self.writeLength(out);        
        out.write("Content-length: " + str(self.totalLen) + "\n");
        out.write("\n");
        self.writeContent(out);
        out.write("\n\n");
        
EmptyProps = SvnProperties()

class SvnAutoProps:
    def __init__(self, filename):
        self.autoProps = {}
        self.load(filename)
        
    def load(self, filename):
        info("Loading svn auto properties from " + filename)
        
        file = open(filename,'r')
        for line in file:
            try: pattern, str = line.strip().split(" = ")
            except: continue
            props = SvnProperties()
            self.autoProps[pattern] = props
            for avp in str.split(";"):
                fields = avp.split("=")
                key = fields[0]
                if len(fields) > 1: 
                    value = fields[1]                    
                else:
                    value = ""
                props.set(key, value)
        file.close()
                
    def getProps(self, filepath):
        filename = os.path.basename(filepath)
        for pattern, props in self.autoProps.iteritems():
            if fnmatch.fnmatch(filename, pattern):
                return props
        return EmptyProps

class CCRecord:
    pass

class CCHistoryParser:
    def __init__(self):
        self.prevline = ""

    def parseLabels(self, s):
        # format: (label1, label2, label3)
        if len(s) > 0 and s.startswith("(") and s.endswith(")"):        
            return s[1:-1].split(", ")
        else:
            return [] 
            
    def processLine(self, line):
        if len(self.prevline) > 0:
            line = line + "\n" + self.prevline
        
        fields = line.split(HISTORY_FIELD_SEPARATOR)
        
        if len(fields) < 10:
            self.prevline = line;
            return None;
        elif len(fields) > 10:
            error("Wrong history line: " + line)
            self.prevline = ""
            return None
        self.prevline = ""
        
        # 20090729.162424;path/to/dir;/main/branch/another/1;checkin;(LABEL_1, LABEL2);;directory version;user1;Added file element file.cpp;
        
        ccRecord = CCRecord()
        ccRecord.date = time.strptime(fields[0], CC_DATE_FORMAT)
        ccRecord.path = os.path.normpath(fields[1]);
        ccRecord.revision = fields[2];
        ccRecord.operation = fields[3];
        ccRecord.labels = self.parseLabels(fields[4]);
        ccRecord.type = fields[6];
        ccRecord.author = fields[7];
        ccRecord.comment = fields[8];
        
        revisionParts = ccRecord.revision.split('/')
        if len(revisionParts) > 0:
            ccRecord.branchNames = revisionParts[1:-1]
            ccRecord.revNumber = revisionParts[-1]
        else:
            ccRecord.branchNames = []
            ccRecord.revNumber = "-1"
        return ccRecord

def writeContentLength(out, len):
    out.write("Content-length: " + str(len) + "\n");

def writeTextContentLength(out, len):
    out.write("Text-content-length: " + str(len) + "\n");

def writeNodePath(out, nodePath):
    out.write("Node-path: " + nodePath + "\n");

def writeNodeKind(out, nodeKind):
    out.write("Node-kind: " + nodeKind + "\n");

def writeNodeAction(out, nodeAction):
    out.write("Node-action: " + nodeAction + "\n");

def calculateLengthAndChecksum(filename):
    textContentLength = 0;
    md = hashlib.md5()        
    file = open(filename, 'rb')
    while 1:
        s = file.read(FILEREAD_CHUNKSIZE)
        if s: 
            md.update(s)
            textContentLength += len(s)
        else: break
    file.close()
    checksum = md.hexdigest()
    return (textContentLength, checksum)

def writeContent(out, filename):
    file = open(filename, 'rb')                             
    while 1:
        s = file.read(FILEREAD_CHUNKSIZE)
        if s: out.write(s);
        else: break
    file.close()

def dumpSvnFile(out, action, path, props, contentFilename):
    writeNodePath(out, path);
    writeNodeKind(out, "file");
    writeNodeAction(out, action);
    
    props.writeLength(out);
    
    textContentLength, checksum = calculateLengthAndChecksum(contentFilename);
    writeTextContentLength(out, textContentLength);
    out.write("Text-content-md5: " + checksum + "\n");
    writeContentLength(out, textContentLength + props.totalLen );
    out.write("\n");
    
    props.writeContent(out);
    
    writeContent(out, contentFilename);
    out.write("\n\n");

def dumpSvnCopy(out, kind, copyfromPath, copyfromRev, target):
    writeNodePath(out, target);
    writeNodeKind(out, kind);
    writeNodeAction(out, "add");
    out.write("Node-copyfrom-rev: " + str(copyfromRev) + "\n");    
    out.write("Node-copyfrom-path: " + toUTF8(copyfromPath) + "\n");
    out.write("\n");        
        
def dumpSvnDir(out, path):
    writeNodePath(out, path);
    writeNodeKind(out, "dir");
    writeNodeAction(out, "add");
    out.write("\n");    

def dumpSvnDelete(out, path):
    writeNodePath(out, path);    
    writeNodeAction(out, "delete");
    out.write("\n");                

       
def getSvnBranchPath(branch):
    return "branches/" + branch

def getSvnTagPath(tag):
    return "tags/" + tag

class SvnRevisionProps:
    def __init__(self):
        self.properties = SvnProperties()
    
    def reset(self):
        self.properties.reset()
            
    def dump(self, out):
        self.properties.dump(out);
        
    def setAuthor(self, author):
        self.properties.set("svn:author", toUTF8(author));
    
    def setDate(self, date):
        self.properties.set("svn:date", time.strftime(SVN_DATE_FORMAT, date))
    
    def setMessage(self, message):
        self.properties.set("svn:log", toUTF8(message));
        
    def setCCRevision(self, ccrevision):
        self.properties.set("ClearcaseRevision", ccrevision);
        
    def setCCLabels(self, cclabels):
        labelStr = ", ".join(cclabels)
        self.properties.set("ClearcaseLabels", labelStr);


class FileSet(set):
    def __init__(self, root):
        self.root = root
        
    def getAbsolutePath(self, path):
        return self.root + "/" + path

class WriteStream:
    def __init__(self, file):
        self.enabled = True
        self.file = file
        pass
    
    def enable(self):
        self.enabled = True
    
    def disable(self):
        self.enabled = False
        
    def disabled(self):
        return self.enabled == False
        
    def write(self, data):
        if self.enabled:
            self.file.write(data)
    
               
class Converter:
    def __init__(self, dumpfile, labels, branches, autoProps):
        self.autoProps = autoProps        
        self.labels = labels                
        if self.labels is not None:
            self.checklabels = self.labels
        else:
            self.checklabels = set()    
        self.branches = branches
        self.out = WriteStream(dumpfile)
        
        self.svnTree = {} # branch/label -> FileSet
        self.ccTree = set() # (ccpath, ccrev)
        self.svnRevNum = 1 
        self.cachedir = CACHE_DIR
        self.revProps = SvnRevisionProps()       
        
        self.out.write("SVN-fs-dump-format-version: 2\n\n")
        
        if DUMP_SINCE_DATE is not None:
            self.out.disable()
        
        if SVN_CREATE_BRANCHES_TAGS_DIRS:
            self.dumpRevisionHeader()
            dumpSvnDir(self.out, getSvnBranchPath(""))
            dumpSvnDir(self.out, getSvnTagPath(""))
        
    def dumpRevisionHeader(self):
        self.out.write("Revision-number: " + str(self.svnRevNum) + "\n");
        self.svnRevNum += 1
        self.revProps.dump(self.out)
    
    def setRevisionProps(self, ccRecord):
        # self.revProps.reset() - not required since we are overwriting the same keys each time        
        self.revProps.setMessage(ccRecord.comment)
        self.revProps.setAuthor(ccRecord.author)
        self.revProps.setDate(ccRecord.date)    
        self.revProps.setCCRevision(ccRecord.revision)
        #self.revProps.setCCLabels(ccRecord.labels)
        
    def dumpFile(self, ccRecord, action, symlink=False):
        contentFilename = self.getFile(ccRecord.path, ccRecord.revision, symlink)
        props = self.autoProps.getProps(ccRecord.svnpath)
        if symlink and action is "add":
            props.set("svn:special", "*")
        dumpSvnFile(self.out, action, ccRecord.svnpath, props, contentFilename)
        
    def createParentDirs(self, fileSet, path):
        dir = os.path.dirname(path)
        if dir and dir not in fileSet:
            self.createParentDirs(fileSet, dir)
            dirpath = fileSet.getAbsolutePath(dir)
            dumpSvnDir(self.out, dirpath)
            fileSet.add(dir)
    
    def getTagFileset(self, label):
        fileSet = self.svnTree.get(label)
        if fileSet is None:
            fileSet = FileSet(getSvnTagPath(label))
            self.svnTree[label] = fileSet
            dumpSvnDir(self.out, fileSet.root)
        return fileSet
    
    def processLabels(self, ccRecord, updateLabels=True):
        self.ccTree.add( (ccRecord.path, ccRecord.revision) )
        first = True
        copyfromRev = self.svnRevNum-1
        for cclabel in ccRecord.labels:
            if self.labels is None or cclabel in self.labels:
                
                if first:
                    self.dumpRevisionHeader()
                    first = False
                    
                fileSet = self.getTagFileset(cclabel)
                    
                self.createParentDirs(fileSet, ccRecord.path)
                
                copyfromPath = ccRecord.svnpath
                copytoPath = fileSet.getAbsolutePath(ccRecord.path)
                
                dumpSvnCopy(self.out, "file", copyfromPath, copyfromRev, copytoPath)
                
                if self.labels is None and updateLabels:
                    self.checklabels.add(cclabel) # will be used in completeLabels phase
        pass
        
    def process(self, ccRecord):
        #    OPERATION;TYPE
        #    checkin;directory version
        #    checkin;version
        #    mkbranch;directory version
        #    mkbranch;version
        #    mkelem;directory version - means version 0
        #    mkelem;version    - means version 0
        #    mkslink;symbolic link
        # not of interest:
        #    **null operation kind**;file element
        #    checkout;directory version
        #    checkout;version
        #    lock;branch
        #    mkbranch;branch
        #    mkelem;branch
        #    mkelem;directory element
        #    mkelem;file element
        
        if ccRecord.path == ".": return
        
        type = ccRecord.type
        operation = ccRecord.operation
        
        ccRecord.svnbranch = len(ccRecord.branchNames) > 0 and ccRecord.branchNames[-1] or "unknown" 
        ccRecord.svnpath = getSvnBranchPath(ccRecord.svnbranch) + "/" + ccRecord.path
        
        if self.branches is not None and ccRecord.svnbranch not in self.branches:
            return            
                
        if DUMP_SINCE_DATE is not None and self.out.disabled() and ccRecord.date > DUMP_SINCE_DATE:
            self.out.enable()
            
        self.setRevisionProps(ccRecord)
        
        if type == "version": # file
            if operation == "checkin" or operation == "mkbranch" or operation == "mkelem":
                # create or modify file                 
                branchFileSet = self.svnTree.get(ccRecord.svnbranch)
                if branchFileSet is not None:
                    # branch is already known
                    self.dumpRevisionHeader()
                    if ccRecord.path in branchFileSet:
                        # file is already in the set - svn modify
                        self.dumpFile(ccRecord, "change")
                        pass
                    else:
                        # new file in branch - svn add
                        self.createParentDirs(branchFileSet, ccRecord.path)                      
                        self.dumpFile(ccRecord, "add")
                        branchFileSet.add(ccRecord.path)
                        pass
                    
                    self.processLabels(ccRecord)
                    pass
                else:
                    # new branch
                    copyfromRev = self.svnRevNum - 1                    
                    self.dumpRevisionHeader()                    
                    if len(ccRecord.branchNames) < 2:
                        # new top level branch
                        newBranchFileSet = FileSet(getSvnBranchPath(ccRecord.svnbranch))
                        self.svnTree[ccRecord.svnbranch] = newBranchFileSet     
                        
                        dumpSvnDir(self.out, newBranchFileSet.root)                     
                        pass
                    else:
                        parentSvnBranch = ccRecord.branchNames[-2]
                        
                        parentBranchFileSet = self.svnTree.get(parentSvnBranch)
                        if parentBranchFileSet:
                            # operation - svn cp
                            copyfromPath = getSvnBranchPath(parentSvnBranch)
                            copytoPath = getSvnBranchPath(ccRecord.svnbranch)
                            
                            newBranchFileSet = parentBranchFileSet.copy()
                            newBranchFileSet.root = copytoPath 
                            self.svnTree[ccRecord.svnbranch] = newBranchFileSet
                                                        
                            dumpSvnCopy(self.out, "dir", copyfromPath, copyfromRev, copytoPath)
                            
                        else:
                            error("ClearCase history is corrupted: child branch appeared before the parent one for file " +
                                  ccRecord.path + "@@" + ccRecord.revision)                            
                            if askYesNo("Create branch anyway and ignore the error? (or exit)"):
                                newBranchFileSet = FileSet(getSvnBranchPath(ccRecord.svnbranch))
                                self.svnTree[ccRecord.svnbranch] = newBranchFileSet
                                dumpSvnDir(self.out, newBranchFileSet.root)
                            else:
                                sys.exit(1)
                        pass
                                    
                    if ccRecord.path in newBranchFileSet:
                        # file is already in the set - svn modify if cc version is not 0
                        if ccRecord.revNumber != "0":
                            self.dumpFile(ccRecord, "change")
                        pass
                    else:
                        # new file in branch - svn add
                        self.createParentDirs(newBranchFileSet, ccRecord.path)                      
                        self.dumpFile(ccRecord, "add")
                        newBranchFileSet.add(ccRecord.path)
                        pass
                    
                    self.processLabels(ccRecord)
                    pass                                
                pass
            
        elif type == "directory version":
            if operation == "checkin" or operation == "mkbranch" or operation == "mkelem":
                # new or modify dir
                branchFileSet = self.svnTree.get(ccRecord.svnbranch)
                if branchFileSet:
                    # branch is already known
                    if ccRecord.path in branchFileSet:
                        # dir is already in the set - it must be adding or removing some files
                        # if some file is removed from the dir - we will not get any history for it
                        # unless it resurrected after - we will ignore this case
                        pass
                    else:
                        # new dir in the branch
                        self.dumpRevisionHeader()
                        self.createParentDirs(branchFileSet, ccRecord.path)
                        dumpSvnDir(self.out, ccRecord.svnpath)                        
                        branchFileSet.add(ccRecord.path)
                        pass
                    # save the dir version in cc tree for label processing stage
                    self.ccTree.add( (ccRecord.path, ccRecord.revision) )
                    pass
                else:
                    # new branch for the dir - wait until there are files in the branch
                    # do nothing                                     
                    pass
                pass
        elif type == "symbolic link" and operation == "mkslink":
            # just get the latest version of the file - we can not track the history of the link
            ccRecord.svnbranch = PUT_CCLINKS_TO_BRANCH
            ccRecord.svnpath = getSvnBranchPath(ccRecord.svnbranch) + "/" + ccRecord.path
            branchFileSet = self.svnTree.get(ccRecord.svnbranch)
            if branchFileSet is not None:
                if ccRecord.path in branchFileSet:
                    self.dumpRevisionHeader()
                    self.dumpFile(ccRecord, "change", symlink=True)                        
                    pass
                else:
                    self.dumpRevisionHeader()
                    self.createParentDirs(branchFileSet, ccRecord.path)
                    self.dumpFile(ccRecord, "add", symlink=True)                
                    branchFileSet.add(ccRecord.path)
                    pass
                pass
            else:
                warn("The branch " + ccRecord.svnbranch + " does not exists. Skip the link " + ccRecord.path)
                pass
            pass
        pass
    
    def getFile(self, path, revision, symlink=False):
        ccfile = path
        localfile = os.path.normpath(self.cachedir + "/" + path)
        if revision: 
            ccfile = ccfile + "@@" + revision
            localfile = os.path.normpath(localfile + "/" + revision)
        localfileDir = os.path.dirname(localfile)
        if not os.path.exists(localfileDir):
            os.makedirs(localfileDir, mode=0777)
         
        cacheExists = os.path.exists(localfile)
        if cacheExists and CHECK_ZEROSIZE_CACHEFILE:
            cacheExists = os.path.getsize(localfile) > 0
         
        if not cacheExists:
            if symlink:
                symlinkfile = os.path.normpath(CC_VOB_DIR + "/" + ccfile)
                if os.path.islink(symlinkfile):
                    content = os.readlink(symlinkfile)
                    outfile = open(localfile, 'wb')
                    outfile.write("link " + content)
                    outfile.close()
                    pass
                else:
                    raise RuntimeError("File " + symlinkfile + " is not a symbolic link")
            else: 
                cmd = CLEARTOOL + " get -to '" + localfile + "' '" + ccfile + "'"
                (status, out) = shellCmd(cmd, cwd=CC_VOB_DIR)
                if status == "ignore":
                    if not os.path.exists(localfile): open(localfile, 'w').close()
        return localfile
    
    def getFileDetails(self, ccrevfile):
        
        localfile = os.path.normpath(self.cachedir + "/" + ccrevfile.replace('@@', '/') + "_descr")
        localfileDir = os.path.dirname(localfile)
        if not os.path.exists(localfileDir):
            os.makedirs(localfileDir, mode=0777)
        
        outStr = ""
        cacheExists = os.path.exists(localfile) and os.path.getsize(localfile) > 0
        if cacheExists:
            with open(localfile, 'r') as file:
                for line in file:
                    outStr += line
        else:
            cmd = CLEARTOOL + " descr -fmt '" + HISTORY_FORMAT + "' " + ccrevfile
            (status, outStr) = shellCmd(cmd, cwd=CC_VOB_DIR)
            with open(localfile, 'w') as file:
                file.write(outStr)                    
        return outStr
        
    def getLabelContent(self, label):       
        labelFilename = os.path.join(CACHE_DIR, label)
        if not os.path.exists(labelFilename):
            cmd = CLEARTOOL + " find . -ver 'version(" + label + ")' -print"
            shellCmd(cmd, cwd=CC_VOB_DIR, outfile=labelFilename)
        return labelFilename
    
    
    def saveConfigSpec(self, file):                
        cmd = CLEARTOOL + " catcs"        
        shellCmd(cmd, outfile=file)
            
    def setConfigSpec(self, file):
        cmd = CLEARTOOL + " setcs " + file        
        shellCmd(cmd)
    
    def setLabelSpec(self, label):
        with open(CCVIEW_TMPFILE, 'w') as file:
                file.write("element * " + label + "\n")        
        self.setConfigSpec(CCVIEW_TMPFILE)        
        
    def completeLabels(self):
        # we need to add to labels those files that are not visible from ClearCase view
        # these are the files that were removed or renamed
    
        info("Checking labels")
        
        parser = CCHistoryParser()
        ccRecord = CCRecord()

        if self.checklabels:
            self.saveConfigSpec(CCVIEW_CONFIGSPEC)
            
        for label in self.checklabels:   
            info("Checking " + label)
                  
            self.setLabelSpec(label)
            try:            
                labelFilename = self.getLabelContent(label)            
                with open(labelFilename, 'r') as file:            
                    for line in file:
                        ccrevfile = line.strip()                        
                        try:
                            (path, revision) = ccrevfile.split('@@')
                        except:
                            warn("label content file " + ccrevfile + " has no revision after @@")
                            continue                                        
                        if path == ".": continue
                        path = os.path.normpath(path)
                        
                        if (path, revision) not in self.ccTree:   
                            details = self.getFileDetails(ccrevfile)
                            ccRecord = parser.processLine(details)
                            if ccRecord and ccRecord.type == "version": # file   
                                
                                if DUMP_SINCE_DATE is not None and ccRecord.date > DUMP_SINCE_DATE:
                                    self.out.enable()
                                else:
                                    self.out.disable()
                                                       
                                info("Found file " + path + "@@" + revision)                            
                                self.setRevisionProps(ccRecord)
                                self.dumpRevisionHeader()
                                fileSet = self.getTagFileset(label)
                                self.createParentDirs(fileSet, ccRecord.path)
                                
                                ccRecord.svnpath = fileSet.getAbsolutePath(ccRecord.path)                      
                                self.dumpFile(ccRecord, "add")
                                fileSet.add(ccRecord.path)
                                
                                if label in ccRecord.labels:
                                    ccRecord.labels.remove(label)                            
                                self.processLabels(ccRecord, updateLabels=False)
                            else:
                                self.ccTree.add( (path, revision) )
            except KeyboardInterrupt, e:
                raise e
            except:
                error(str(sys.exc_info()[1]))
        
        if self.checklabels:
            self.setConfigSpec(CCVIEW_CONFIGSPEC)
        pass

############# main functions ######################
    
def getCCHistory(filename):
    info("Loading CC history to " + filename)
    
    if os.path.exists(filename):
        info("File " + filename + " already exists")
        if askYesNo("Use this file?"):
            return filename        
    
    cmd = CLEARTOOL + " lshistory -recurse -fmt '" + HISTORY_FORMAT + "'"
    shellCmd(cmd, cwd=CC_VOB_DIR, outfile=filename)
    pass

def readList(filename):
    resList = None
    if filename:
        info("Reading " + filename)
        resList = set()
        with open(filename, 'r') as file:
            for line in file:
                resList.add(line.strip())
    return resList

def main():
    
    try:
    
        labels = readList(CC_LABELS_FILE)
        
        branches = readList(CC_BRANCHES_FILE)
    
        getCCHistory(HISTORY_FILE)
        
        autoProps = SvnAutoProps(SVN_AUTOPROPS_FILE)
        
        info("Processing ClearCase history, creating svn dump " + SVN_DUMP_FILE)
    
        with open(SVN_DUMP_FILE, 'wb') as dumpfile:
        
            converter = Converter(dumpfile, labels, branches, autoProps)
            
            parser = CCHistoryParser()
            
            with open(HISTORY_FILE, 'rb') as historyFile: 
                for line in rlines(historyFile): # reading lines in reverse order
                    ccRecord = parser.processLine(line)
                    if ccRecord:
                        converter.process(ccRecord)
            
            converter.completeLabels()
        
        info("Completed")
            
    except SystemExit:
        info("Exiting")
    except KeyboardInterrupt, e:
        error("Interrupted by user")    
    except:
        error(str(sys.exc_info()[1]))
    
if __name__ == "__main__": 
    main()
