cc2svn
======

Fork of cc2svn working on windows with some extra functionalities. Clearcase to SVN.

Original source code : https://code.google.com/p/cc2svn/

Features
--------

cc2svn tool converts ClearCase view files with all history and given labels to SVN dump.
The dump can be loaded by SVN using 'cat svndump.txt | svnadmin load' command.
Features:

    transfers history of changes for files saving the date, author and comment for each revision
    converts all/some/none branches (configurable)
    converts all/some/none labels (configurable)
	__allows to filter imported directories__
    incremental dump mode
    retry/ignore failed CC commands
    cache for ClearCase files
    tested on Linux/Solaris and __Windows__

Main points
-----------

The tool uses the current ClearCase view to list the CC history (ct lshi -rec) then it goes through the history and processes each record.
That means that the tool does not transfer those files that are not visible from the current CC view.
However the tool transfers all CC labels to SVN tags correctly. For that in the second phase it sets config_spec of the current view to match the label (element * LABEL) for each given label and checks that no files are lost during the first phase.

__WARNING__: Side effect - the tool changes the config_spec of the current working ClearCase view. Do not use the view during the tool work.

All branches except the /main are created using 'svn cp' command basing on the CC parent branch.

There is a difference in creating the branches in ClearCase and SVN:
SVN copies all files from parent branch to the target like: svn cp branches/main branches/dev_branch
ClearCase creates the actual branch for file upon checkout operation only.
In other words the tool can't guarantee the content of /branches will be exactly like in ClearCase.
But the tool guarantees the labels are transferred correctly.

The tool uses cache directory to place ClearCase version files there. The cache speeds up the transfer process in many times in subsequent attempts (up to 10 times). It may be recommended to start the tool 2 days before the actual transfer loading all files to the cache. So only new versions appeared during these days will be retrieved from ClearCase in the day of the transfer.
Actually the tool caches any data retrieved from ClearCase including the history file.

The tool provides the possibility to retry/ignore any ClearCase command if error occurs.
The tool will put empty file to the cache if you ignore ClearCase retrieving operation error.

Timing: CC repository of 5 GB (~120.000 revisions) is converted in ~1 hour using the pre-cached files. 